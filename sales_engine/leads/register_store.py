"""
Lead Register Store: Persistent SQLite storage for 13-field lead records.
Integrates seamlessly with CandidateStore while maintaining complete separation
of editorial fields from historical suppression/outreach constraints.
Safe on fresh databases, candidate-only databases, and historical databases.
"""

import sqlite3
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
import os

from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.candidates.candidate_store import normalize_company_name, canonicalize_domain

logger = logging.getLogger(__name__)


class LeadRegisterStore:
    def __init__(self, db_path: str = "data/sales_outreach.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS lead_register_records (
                    candidate_id TEXT PRIMARY KEY,
                    record_number INTEGER,
                    priority_level TEXT DEFAULT 'B',
                    priority_rationale TEXT,
                    company_name TEXT NOT NULL,
                    legal_identity TEXT,
                    geographic_location TEXT,
                    telephone_numbers TEXT,
                    business_activities TEXT,
                    official_website TEXT,
                    responsible_person TEXT,
                    descriptions_and_notes TEXT,
                    job_advertisement_links TEXT,
                    other_links_and_references TEXT,
                    contact_email_addresses TEXT,
                    provenance_source TEXT DEFAULT 'manual_entry',
                    research_date TEXT,
                    verification_status TEXT DEFAULT 'Unverified',
                    confidence_score REAL DEFAULT 0.0,
                    evidence_urls_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_lead_register_rec_num ON lead_register_records(record_number);
                CREATE INDEX IF NOT EXISTS idx_lead_register_priority ON lead_register_records(priority_level);
            """)

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        """Safely checks if a table exists in SQLite schema."""
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,)
        ).fetchone()
        return bool(row)

    def get_record(self, candidate_id: str) -> Optional[LeadRegisterRecord]:
        """Fetch lead register record by candidate_id, synthesizing from observation/candidate if not yet explicitly stored."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM lead_register_records WHERE candidate_id = ?",
                (candidate_id,)
            ).fetchone()
            if row:
                return self._row_to_record(row)

            # Check if candidate_companies exists before querying
            if not self._table_exists(conn, "candidate_companies"):
                return None

            cand_row = conn.execute(
                "SELECT rowid, * FROM candidate_companies WHERE candidate_id = ?",
                (candidate_id,)
            ).fetchone()
            if not cand_row:
                return None

            return self._synthesize_record_from_candidate(conn, cand_row)

    def save_record(self, record: LeadRegisterRecord) -> LeadRegisterRecord:
        """Persists or updates an explicit 13-field record."""
        now = datetime.now(timezone.utc).isoformat()
        record.updated_at = now
        if not record.created_at:
            record.created_at = now

        # Assign next record number if missing
        if record.record_number is None:
            record.record_number = self._get_next_record_number()

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO lead_register_records (
                    candidate_id, record_number, priority_level, priority_rationale,
                    company_name, legal_identity, geographic_location, telephone_numbers,
                    business_activities, official_website, responsible_person,
                    descriptions_and_notes, job_advertisement_links, other_links_and_references,
                    contact_email_addresses, provenance_source, research_date,
                    verification_status, confidence_score, evidence_urls_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    record_number = COALESCE(excluded.record_number, lead_register_records.record_number),
                    priority_level = excluded.priority_level,
                    priority_rationale = excluded.priority_rationale,
                    company_name = excluded.company_name,
                    legal_identity = excluded.legal_identity,
                    geographic_location = excluded.geographic_location,
                    telephone_numbers = excluded.telephone_numbers,
                    business_activities = excluded.business_activities,
                    official_website = excluded.official_website,
                    responsible_person = excluded.responsible_person,
                    descriptions_and_notes = excluded.descriptions_and_notes,
                    job_advertisement_links = excluded.job_advertisement_links,
                    other_links_and_references = excluded.other_links_and_references,
                    contact_email_addresses = excluded.contact_email_addresses,
                    provenance_source = excluded.provenance_source,
                    research_date = COALESCE(excluded.research_date, lead_register_records.research_date),
                    verification_status = excluded.verification_status,
                    confidence_score = excluded.confidence_score,
                    evidence_urls_json = excluded.evidence_urls_json,
                    updated_at = excluded.updated_at
            """, (
                record.candidate_id,
                record.record_number,
                record.priority_level,
                record.priority_rationale,
                record.company_name,
                record.legal_identity,
                record.geographic_location,
                record.telephone_numbers,
                record.business_activities,
                record.official_website,
                record.responsible_person,
                record.descriptions_and_notes,
                record.job_advertisement_links,
                record.other_links_and_references,
                record.contact_email_addresses,
                record.provenance_source,
                record.research_date,
                record.verification_status,
                record.confidence_score,
                json.dumps(record.evidence_urls),
                record.created_at,
                record.updated_at,
            ))
            conn.commit()

        return record

    def list_records(self, limit: int = 1000, offset: int = 0, query: Optional[str] = None, priority_filter: Optional[str] = None) -> List[LeadRegisterRecord]:
        """Lists records across all candidates, prioritizing explicitly stored register records and synthesizing remaining."""
        with self._get_connection() as conn:
            if not self._table_exists(conn, "candidate_companies"):
                # Return explicitly saved lead_register_records if candidate_companies does not exist
                rows = conn.execute(
                    "SELECT * FROM lead_register_records ORDER BY record_number ASC LIMIT ? OFFSET ?",
                    (limit, offset)
                ).fetchall()
                records = [self._row_to_record(r) for r in rows]
                if query:
                    ql = query.lower()
                    records = [
                        r for r in records
                        if ql in f"{r.company_name} {r.official_website} {r.geographic_location} {r.descriptions_and_notes} {r.contact_email_addresses} {r.telephone_numbers} {r.responsible_person}".lower()
                    ]
                return records

            cand_rows = conn.execute(
                "SELECT rowid, * FROM candidate_companies ORDER BY rowid ASC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()

            records = []
            for r in cand_rows:
                cid = r["candidate_id"]
                reg_row = conn.execute(
                    "SELECT * FROM lead_register_records WHERE candidate_id = ?",
                    (cid,)
                ).fetchone()
                if reg_row:
                    rec = self._row_to_record(reg_row)
                else:
                    rec = self._synthesize_record_from_candidate(conn, r)

                if query:
                    ql = query.lower()
                    haystack = f"{rec.company_name} {rec.official_website} {rec.geographic_location} {rec.descriptions_and_notes} {rec.contact_email_addresses} {rec.telephone_numbers} {rec.responsible_person}".lower()
                    if ql not in haystack:
                        continue

                if priority_filter and priority_filter.strip():
                    pf = priority_filter.strip().upper()
                    if rec.priority_level != pf:
                        continue

                records.append(rec)

            return records

    def _get_next_record_number(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT MAX(record_number) FROM lead_register_records").fetchone()
            max_stored = row[0] if (row and row[0] is not None) else 0
            
            # Check candidate count if table exists
            cand_cnt = 0
            if self._table_exists(conn, "candidate_companies"):
                cnt_row = conn.execute("SELECT COUNT(1) FROM candidate_companies").fetchone()
                if cnt_row:
                    cand_cnt = cnt_row[0]

            return max(max_stored, cand_cnt) + 1

    def _synthesize_record_from_candidate(self, conn: sqlite3.Connection, cand_row: sqlite3.Row) -> LeadRegisterRecord:
        cid = cand_row["candidate_id"]
        rowid = cand_row["rowid"]
        name = cand_row["company_name"]
        dom = cand_row["company_domain"]
        legal_form = cand_row["legal_form"]
        tier = cand_row["company_tier"]

        # Parse tier A/B/C
        tier_str = "B"
        if tier:
            t_upper = str(tier).upper()
            if "TIER_1" in t_upper or t_upper == "A":
                tier_str = "A"
            elif "TIER_3" in t_upper or t_upper == "C":
                tier_str = "C"

        # Check observation for rich historical or pilot payload
        obs_row = None
        if self._table_exists(conn, "discovery_observations"):
            obs_row = conn.execute(
                "SELECT raw_payload_json, extracted_data_json, source_id, source_url FROM discovery_observations WHERE candidate_id = ? LIMIT 1",
                (cid,)
            ).fetchone()

        phones = None
        business_act = None
        website = f"https://{dom}" if dom else None
        contacts_str = None
        notes = None
        job_links = None
        other_links = None
        emails_str = None
        p_iva = None
        priority_rationale = None
        provenance = "candidate_store"
        # Semantics: Historical workbook imports and syntheses are unverified against external public sites
        verification_status = "Unverified"
        confidence = 0.50

        # Location fallback
        loc_parts = [cand_row["city"], cand_row["region"], cand_row["country"]]
        loc = ", ".join([p for p in loc_parts if p]) or None

        if obs_row:
            source_id = obs_row["source_id"]
            if source_id == "pilot_research":
                provenance = "pilot_research"
                verification_status = "OPERATOR_REVIEWED"
                confidence = 0.85
            elif source_id == "operator_research":
                provenance = "operator_researched"
                verification_status = "EXTRACTED"
                confidence = 0.50
            elif "historical" in str(source_id) or "historical_seed" in str(source_id):
                provenance = "historical_import"
                verification_status = "Unverified"
                confidence = 0.50

            ed = json.loads(obs_row["extracted_data_json"]) if obs_row["extracted_data_json"] else {}
            rp = json.loads(obs_row["raw_payload_json"]) if obs_row["raw_payload_json"] else {}

            row_num = ed.get("row_number") or rowid
            phones = ed.get("phone_numbers") or ed.get("phone")
            business_act = ed.get("business_topic") or (", ".join(ed.get("bim_services", [])) if ed.get("bim_services") else None)
            website = ed.get("raw_website_cell") or obs_row["source_url"] or website
            notes = ed.get("notes") or rp.get("\u062a\u0648\u0636\u06cc\u062d\u0627\u062a \u0648 \u06cc\u0627\u062f\u062f\u0627\u0634\u062a\u200c\u0647\u0627")
            job_links = ed.get("job_link")
            other_links = ed.get("other_links")
            p_iva = ed.get("vat_number")
            priority_rationale = ed.get("priority_reason")
            if ed.get("raw_location"):
                loc = ed["raw_location"]
            if ed.get("email"):
                emails_str = ed["email"]
        else:
            row_num = rowid

        # Safely lookup historical_contacts if table exists
        if not contacts_str and dom and self._table_exists(conn, "historical_contacts"):
            try:
                cont_rows = conn.execute(
                    "SELECT contact_name, email FROM historical_contacts WHERE canonical_domain = ?",
                    (dom,)
                ).fetchall()
                if cont_rows:
                    names = [r["contact_name"] for r in cont_rows if r["contact_name"]]
                    if names:
                        contacts_str = "; ".join(names)
                    if not emails_str:
                        e_list = [r["email"] for r in cont_rows if r["email"]]
                        if e_list:
                            emails_str = ", ".join(e_list)
            except Exception as e:
                logger.warning(f"Failed querying historical_contacts: {e}")

        # Safely lookup candidate_contacts table if table exists
        if not contacts_str and self._table_exists(conn, "candidate_contacts"):
            try:
                c_contacts = conn.execute(
                    "SELECT full_name, work_email FROM candidate_contacts WHERE candidate_id = ?",
                    (cid,)
                ).fetchall()
                if c_contacts:
                    contacts_str = "; ".join([c["full_name"] for c in c_contacts if c["full_name"]])
                    if not emails_str:
                        emails_str = ", ".join([c["work_email"] for c in c_contacts if c["work_email"]])
            except Exception as e:
                logger.warning(f"Failed querying candidate_contacts: {e}")

        # Safely lookup links from historical_company_links if table exists
        if not other_links and dom and self._table_exists(conn, "historical_company_links"):
            try:
                link_rows = conn.execute(
                    "SELECT raw_url FROM historical_company_links WHERE canonical_domain = ? AND category IN ('PROJECTS', 'OTHER')",
                    (dom,)
                ).fetchall()
                if link_rows:
                    other_links = " | ".join([r["raw_url"] for r in link_rows if r["raw_url"]])
            except Exception as e:
                logger.warning(f"Failed querying historical_company_links: {e}")

        legal_id = p_iva or legal_form or "Not Found"

        return LeadRegisterRecord(
            candidate_id=cid,
            record_number=row_num,
            priority_level=tier_str,
            priority_rationale=priority_rationale or (cand_row["tier_evidence_json"] if cand_row["tier_evidence_json"] and cand_row["tier_evidence_json"] != '[]' else None),
            company_name=name,
            legal_identity=legal_id,
            geographic_location=loc or "Not Found",
            telephone_numbers=phones or "Not Found",
            business_activities=business_act or (", ".join(json.loads(cand_row["industry_tags_json"])) if cand_row["industry_tags_json"] != '[]' else "Not Found"),
            official_website=website or "Not Found",
            responsible_person=contacts_str or "Not Found",
            descriptions_and_notes=notes or cand_row["manual_review_notes"] or "Not Found",
            job_advertisement_links=job_links or "Not Found",
            other_links_and_references=other_links or "Not Found",
            contact_email_addresses=emails_str or "Not Found",
            provenance_source=provenance,
            verification_status=verification_status,
            confidence_score=confidence,
            created_at=cand_row["first_seen_at"],
            updated_at=cand_row["last_seen_at"],
        )

    def _row_to_record(self, row: sqlite3.Row) -> LeadRegisterRecord:
        ev_urls = []
        if "evidence_urls_json" in row.keys() and row["evidence_urls_json"]:
            try:
                ev_urls = json.loads(row["evidence_urls_json"])
            except Exception:
                pass

        return LeadRegisterRecord(
            candidate_id=row["candidate_id"],
            record_number=row["record_number"],
            priority_level=row["priority_level"],
            priority_rationale=row["priority_rationale"],
            company_name=row["company_name"],
            legal_identity=row["legal_identity"],
            geographic_location=row["geographic_location"],
            telephone_numbers=row["telephone_numbers"],
            business_activities=row["business_activities"],
            official_website=row["official_website"],
            responsible_person=row["responsible_person"],
            descriptions_and_notes=row["descriptions_and_notes"],
            job_advertisement_links=row["job_advertisement_links"],
            other_links_and_references=row["other_links_and_references"],
            contact_email_addresses=row["contact_email_addresses"],
            provenance_source=row["provenance_source"],
            research_date=row["research_date"],
            verification_status=row["verification_status"],
            confidence_score=row["confidence_score"],
            evidence_urls=ev_urls,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
