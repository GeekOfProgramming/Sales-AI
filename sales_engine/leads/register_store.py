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

from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
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

                CREATE TABLE IF NOT EXISTS lead_register_field_reviews (
                    candidate_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    approved_value TEXT NOT NULL,
                    value_checksum TEXT NOT NULL,
                    review_state TEXT DEFAULT 'OPERATOR_APPROVED',
                    reviewed_at TEXT NOT NULL,
                    source_type TEXT DEFAULT 'llm_analysis',
                    snapshot_id TEXT,
                    analysis_id TEXT,
                    evidence_url TEXT,
                    evidence_excerpt TEXT,
                    operator_note TEXT,
                    PRIMARY KEY (candidate_id, field_name)
                );
                CREATE INDEX IF NOT EXISTS idx_field_reviews_cid ON lead_register_field_reviews(candidate_id);
            """)

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        """Safely checks if a table exists in SQLite schema."""
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,)
        ).fetchone()
        return bool(row)

    def get_explicit_record(self, candidate_id: str) -> Optional[LeadRegisterRecord]:
        """Fetch explicitly stored lead register record by candidate_id without synthetic fallback."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM lead_register_records WHERE candidate_id = ?",
                (candidate_id,)
            ).fetchone()
            if row:
                return self._row_to_record(row)
            return None

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

    def query_records(
        self,
        limit: int = 25,
        offset: int = 0,
        query: str = None,
        priority: str = None,
        country: str = None,
        city: str = None,
        status: str = None,
        sort_by: str = "record_number",
        sort_order: str = "asc",
    ) -> Tuple[List[LeadRegisterRecord], int, int]:
        """
        Server-side filtered and paginated query across lead register records and candidate companies.
        Applies SQL-level filtering and deterministic ordering BEFORE pagination.
        Returns: (records, total_matching_count, total_register_count)
        """
        valid_sort_fields = {
            "record_number": "effective_record_number",
            "company_name": "effective_company_name",
            "priority": "effective_priority",
        }
        order_col = valid_sort_fields.get(sort_by, "effective_record_number")
        order_dir = "DESC" if str(sort_order).lower() == "desc" else "ASC"

        with self._get_connection() as conn:
            cand_exists = self._table_exists(conn, "candidate_companies")
            reg_exists = self._table_exists(conn, "lead_register_records")

            if not cand_exists and not reg_exists:
                return [], 0, 0

            if not cand_exists:
                where_clauses = []
                params = []

                if query and query.strip():
                    ql = f"%{query.strip()}%"
                    where_clauses.append("""(
                        company_name LIKE ? OR
                        official_website LIKE ? OR
                        geographic_location LIKE ? OR
                        descriptions_and_notes LIKE ? OR
                        contact_email_addresses LIKE ? OR
                        telephone_numbers LIKE ? OR
                        responsible_person LIKE ?
                    )""")
                    params.extend([ql] * 7)

                if priority and priority.strip():
                    where_clauses.append("UPPER(priority_level) = ?")
                    params.append(priority.strip().upper())

                if status and status.strip():
                    where_clauses.append("verification_status = ?")
                    params.append(status.strip())

                where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

                total_row = conn.execute("SELECT COUNT(1) FROM lead_register_records").fetchone()
                total_register_count = total_row[0] if total_row else 0

                match_row = conn.execute(f"SELECT COUNT(1) FROM lead_register_records {where_sql}", params).fetchone()
                matching_count = match_row[0] if match_row else 0

                records_sql = f"""
                    SELECT * FROM lead_register_records
                    {where_sql}
                    ORDER BY {sort_by if sort_by in ('record_number', 'company_name') else 'record_number'} {order_dir}
                    LIMIT ? OFFSET ?
                """
                page_params = list(params) + [max(0, limit), max(0, offset)]
                rows = conn.execute(records_sql, page_params).fetchall()
                records = [self._row_to_record(r) for r in rows]
                return records, matching_count, total_register_count

            base_cte = """
                WITH unified_leads AS (
                    SELECT 
                        COALESCE(l.candidate_id, c.candidate_id) AS candidate_id,
                        COALESCE(l.record_number, c.rowid) AS effective_record_number,
                        COALESCE(
                            l.priority_level,
                            CASE 
                                WHEN c.company_tier LIKE '%tier_1%' OR c.company_tier = 'A' THEN 'A'
                                WHEN c.company_tier LIKE '%tier_3%' OR c.company_tier = 'C' THEN 'C'
                                ELSE 'B'
                            END
                        ) AS effective_priority,
                        COALESCE(l.company_name, c.company_name) AS effective_company_name,
                        COALESCE(l.geographic_location, '') AS geographic_location,
                        COALESCE(c.country, '') AS country,
                        COALESCE(c.city, '') AS city,
                        COALESCE(c.company_domain, '') AS domain,
                        COALESCE(l.official_website, '') AS reg_website,
                        COALESCE(l.telephone_numbers, '') AS telephone_numbers,
                        COALESCE(l.business_activities, '') AS business_activities,
                        COALESCE(l.responsible_person, '') AS responsible_person,
                        COALESCE(l.descriptions_and_notes, '') AS descriptions_and_notes,
                        COALESCE(l.job_advertisement_links, '') AS job_advertisement_links,
                        COALESCE(l.other_links_and_references, '') AS other_links_and_references,
                        COALESCE(l.contact_email_addresses, '') AS contact_email_addresses,
                        COALESCE(l.verification_status, 'Unverified') AS verification_status,
                        COALESCE(l.provenance_source, 'candidate_store') AS provenance_source,
                        l.candidate_id AS is_explicit,
                        c.rowid AS cand_rowid
                    FROM candidate_companies c
                    FULL OUTER JOIN lead_register_records l ON c.candidate_id = l.candidate_id
                )
            """

            where_clauses = []
            params = []

            if query and query.strip():
                ql = f"%{query.strip()}%"
                where_clauses.append("""(
                    effective_company_name LIKE ? OR
                    domain LIKE ? OR
                    reg_website LIKE ? OR
                    contact_email_addresses LIKE ? OR
                    telephone_numbers LIKE ? OR
                    descriptions_and_notes LIKE ? OR
                    responsible_person LIKE ? OR
                    business_activities LIKE ? OR
                    geographic_location LIKE ? OR
                    country LIKE ? OR
                    city LIKE ?
                )""")
                params.extend([ql] * 11)

            if priority and priority.strip():
                where_clauses.append("effective_priority = ?")
                params.append(priority.strip().upper())

            if country and country.strip():
                c_val = country.strip()
                if c_val.lower() == "unknown":
                    where_clauses.append("(country IS NULL OR country = '' OR country = 'Not Found')")
                else:
                    where_clauses.append("LOWER(country) = LOWER(?)")
                    params.append(c_val)

            if city and city.strip():
                ct_val = city.strip()
                if ct_val.lower() == "unknown":
                    where_clauses.append("(city IS NULL OR city = '' OR city = 'Not Found')")
                else:
                    where_clauses.append("LOWER(city) = LOWER(?)")
                    params.append(ct_val)

            if status and status.strip():
                where_clauses.append("LOWER(verification_status) = LOWER(?)")
                params.append(status.strip())

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            total_sql = f"{base_cte} SELECT COUNT(1) FROM unified_leads"
            total_register_count = conn.execute(total_sql).fetchone()[0]

            match_sql = f"{base_cte} SELECT COUNT(1) FROM unified_leads {where_sql}"
            matching_count = conn.execute(match_sql, params).fetchone()[0]

            page_sql = f"""
                {base_cte}
                SELECT candidate_id, is_explicit, cand_rowid
                FROM unified_leads
                {where_sql}
                ORDER BY {order_col} {order_dir}, candidate_id ASC
                LIMIT ? OFFSET ?
            """
            page_params = list(params) + [max(0, limit), max(0, offset)]
            id_rows = conn.execute(page_sql, page_params).fetchall()

            records = []
            for r in id_rows:
                cid = r["candidate_id"]
                if r["is_explicit"]:
                    reg_row = conn.execute("SELECT * FROM lead_register_records WHERE candidate_id = ?", (cid,)).fetchone()
                    if reg_row:
                        records.append(self._row_to_record(reg_row))
                        continue
                
                cand_row = conn.execute("SELECT rowid, * FROM candidate_companies WHERE candidate_id = ?", (cid,)).fetchone()
                if cand_row:
                    records.append(self._synthesize_record_from_candidate(conn, cand_row))
                elif r["is_explicit"]:
                    reg_row = conn.execute("SELECT * FROM lead_register_records WHERE candidate_id = ?", (cid,)).fetchone()
                    if reg_row:
                        records.append(self._row_to_record(reg_row))

            return records, matching_count, total_register_count

    def get_filter_options(self) -> Dict[str, Any]:
        """
        Retrieves authoritative distinct filter values (countries, cities by country, verification statuses).
        Returns structured geographic and status options without inventing data.
        """
        with self._get_connection() as conn:
            cand_exists = self._table_exists(conn, "candidate_companies")
            reg_exists = self._table_exists(conn, "lead_register_records")

            countries = set()
            cities_by_country = {}
            all_cities = set()
            statuses = set()

            if cand_exists:
                c_rows = conn.execute("""
                    SELECT DISTINCT country, city 
                    FROM candidate_companies 
                    WHERE (country IS NOT NULL AND country != '') OR (city IS NOT NULL AND city != '')
                """).fetchall()
                for r in c_rows:
                    cntry = (r["country"] or "").strip()
                    ct = (r["city"] or "").strip()
                    if cntry and cntry != "Not Found":
                        countries.add(cntry)
                        if ct and ct != "Not Found":
                            cities_by_country.setdefault(cntry, []).append(ct)
                    if ct and ct != "Not Found":
                        all_cities.add(ct)

            if reg_exists:
                s_rows = conn.execute("SELECT DISTINCT verification_status FROM lead_register_records WHERE verification_status IS NOT NULL").fetchall()
                for r in s_rows:
                    st = (r[0] or "").strip()
                    if st and st != "Not Found":
                        statuses.add(st)

            for k in cities_by_country:
                cities_by_country[k] = sorted(list(set(cities_by_country[k])))

            if not statuses:
                statuses.update(["Unverified", "OPERATOR_REVIEWED", "EXTRACTED"])

            return {
                "countries": sorted(list(countries)),
                "cities_by_country": cities_by_country,
                "all_cities": sorted(list(all_cities)),
                "statuses": sorted(list(statuses)),
                "priorities": ["A", "B", "C"],
            }

    def list_records(self, limit: int = 1000, offset: int = 0, query: Optional[str] = None, priority_filter: Optional[str] = None) -> List[LeadRegisterRecord]:
        """Lists records across all candidates, prioritizing explicitly stored register records and synthesizing remaining."""
        records, _, _ = self.query_records(limit=limit, offset=offset, query=query, priority=priority_filter)
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

    def save_field_review(self, review: FieldReviewRecord) -> FieldReviewRecord:
        """Persists or updates an individual field review record."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO lead_register_field_reviews (
                    candidate_id, field_name, approved_value, value_checksum,
                    review_state, reviewed_at, source_type, snapshot_id,
                    analysis_id, evidence_url, evidence_excerpt, operator_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id, field_name) DO UPDATE SET
                    approved_value = excluded.approved_value,
                    value_checksum = excluded.value_checksum,
                    review_state = excluded.review_state,
                    reviewed_at = excluded.reviewed_at,
                    source_type = excluded.source_type,
                    snapshot_id = excluded.snapshot_id,
                    analysis_id = excluded.analysis_id,
                    evidence_url = excluded.evidence_url,
                    evidence_excerpt = excluded.evidence_excerpt,
                    operator_note = excluded.operator_note
            """, (
                review.candidate_id,
                review.field_name,
                review.approved_value,
                review.value_checksum,
                review.review_state,
                review.reviewed_at,
                review.source_type,
                review.snapshot_id,
                review.analysis_id,
                review.evidence_url,
                review.evidence_excerpt,
                review.operator_note,
            ))
            conn.commit()
        return review

    def get_field_reviews(self, candidate_id: str) -> Dict[str, FieldReviewRecord]:
        """Fetches all persisted field reviews for a candidate, keyed by field_name."""
        with self._get_connection() as conn:
            if not self._table_exists(conn, "lead_register_field_reviews"):
                return {}
            rows = conn.execute(
                "SELECT * FROM lead_register_field_reviews WHERE candidate_id = ?",
                (candidate_id,)
            ).fetchall()
            reviews = {}
            for r in rows:
                reviews[r["field_name"]] = FieldReviewRecord(
                    candidate_id=r["candidate_id"],
                    field_name=r["field_name"],
                    approved_value=r["approved_value"],
                    value_checksum=r["value_checksum"],
                    review_state=r["review_state"],
                    reviewed_at=r["reviewed_at"],
                    source_type=r["source_type"],
                    snapshot_id=r["snapshot_id"],
                    analysis_id=r["analysis_id"],
                    evidence_url=r["evidence_url"],
                    evidence_excerpt=r["evidence_excerpt"],
                    operator_note=r["operator_note"],
                )
            return reviews

    def delete_field_review(self, candidate_id: str, field_name: str) -> bool:
        """Deletes a field review when a field value is modified or revoked."""
        with self._get_connection() as conn:
            if not self._table_exists(conn, "lead_register_field_reviews"):
                return False
            cur = conn.execute(
                "DELETE FROM lead_register_field_reviews WHERE candidate_id = ? AND field_name = ?",
                (candidate_id, field_name)
            )
            conn.commit()
            return cur.rowcount > 0
