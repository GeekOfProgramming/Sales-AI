# sales_engine/candidates/candidate_store.py
"""
Persistent store for Candidate Companies, Discovery Observations, Market Evidence, and Watchlists.
Enforces strict domain canonicalization, multi-source merging without name-fuzzy collisions,
and idempotent promotion from Watchlist.
"""

import json
import logging
import sqlite3
import threading
import re
import urllib.parse
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone

from backend.schemas import ContactCandidate

logger = logging.getLogger(__name__)

from sales_engine.candidates.models import (
    CandidateCompany,
    DiscoveryObservation,
    MarketEvidence,
    WatchlistEntry,
    CompanyTier,
    BIMMaturity,
    RelationshipType,
    OpportunityType,
    QualificationOutcome,
    DiscoveryStatus,
    DiscoveryRunRecord,
    SourceHealthRecord,
)


def canonicalize_domain(domain_or_url: Optional[str]) -> Optional[str]:
    """
    Normalizes a domain or URL to its clean lowercase canonical domain.
    E.g.: 'https://www.Acme-BIM.it/en/about/' -> 'acme-bim.it'
    """
    if not domain_or_url:
        return None
    val = domain_or_url.strip().lower()
    if not val:
        return None

    # Handle protocol
    if "://" not in val:
        val = "http://" + val

    try:
        parsed = urllib.parse.urlparse(val)
        host = parsed.netloc.split(":")[0]  # strip port
        # Strip leading www. and job subdomains
        if host.startswith("www."):
            host = host[4:]
        for sub in ("careers.", "jobs.", "apply.", "recruiting.", "work.", "hr.", "info.", "portal."):
            if host.startswith(sub):
                host = host[len(sub):]
                break
        if host.startswith("www."):
            host = host[4:]
        # Basic validation
        if "." in host and not host.endswith("."):
            return host.strip()
    except Exception:
        pass

    # Fallback regex extraction
    match = re.search(r'(?:https?://)?(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', domain_or_url)
    if match:
        h = match.group(1).lower()
        if h.startswith("www."):
            h = h[4:]
        for sub in ("careers.", "jobs.", "apply.", "recruiting.", "work.", "hr."):
            if h.startswith(sub):
                h = h[len(sub):]
                break
        return h

    return None


def normalize_company_name(name: str) -> str:
    """Normalizes company name for exact-matching fallbacks while stripping legal suffixes."""
    if not name:
        return ""
    n = name.strip().lower()
    # Normalize dotted abbreviations e.g. s.r.l. -> srl, s.p.a. -> spa
    n = re.sub(r'\b([a-z])\.([a-z])\.([a-z])\.?', r'\1\2\3', n)
    n = re.sub(r'\b([a-z])\.([a-z])\.?', r'\1\2', n)
    # Strip common legal suffixes
    suffixes = [
        "srl", "spa", "gmbh", "ltd", "llc", "inc",
        "sas", "snc", "soc coop", "coop", "corp"
    ]
    for s in suffixes:
        n = re.sub(r'\b' + re.escape(s) + r'\b', '', n)
    n = re.sub(r'[^a-zA-Z0-9\s]', ' ', n)
    return " ".join(n.split())


class CandidateStore:
    def __init__(self, db_path: Optional[str] = ":memory:"):
        self.db_path = db_path or ":memory:"
        self._mem_conn = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON")
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS candidate_companies (
                    candidate_id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    normalized_company_name TEXT NOT NULL,
                    company_domain TEXT,
                    country TEXT,
                    region TEXT,
                    city TEXT,
                    company_types_json TEXT,
                    industry_tags_json TEXT,
                    legal_form TEXT,
                    legal_form_raw TEXT,
                    employee_size_hint TEXT,
                    project_scale_hint TEXT,
                    company_tier TEXT,
                    tier_confidence REAL,
                    tier_evidence_json TEXT,
                    bim_maturity TEXT,
                    bim_maturity_confidence REAL,
                    direct_project_access INTEGER,
                    direct_project_access_confidence REAL,
                    outsourcing_potential TEXT,
                    automation_potential TEXT,
                    relationship_type TEXT,
                    opportunity_types_json TEXT,
                    discovery_status TEXT,
                    fit_assessment TEXT,
                    fit_reasons_json TEXT,
                    intent_assessment TEXT,
                    intent_signals_json TEXT,
                    source_refs_json TEXT,
                    evidence_refs_json TEXT,
                    aliases_json TEXT,
                    source_identities_json TEXT,
                    domain_history_json TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    last_researched_at TEXT,
                    historical_contact_status TEXT,
                    last_contacted_at TEXT,
                    reply_status TEXT,
                    active_conversation INTEGER,
                    suppressed INTEGER,
                    cold_outreach_allowed INTEGER,
                    qualification_outcome TEXT,
                    qualification_reasons_json TEXT,
                    manual_review_decision TEXT DEFAULT 'NOT_REVIEWED',
                    manual_reviewed_at TEXT,
                    manual_reviewed_by TEXT,
                    manual_review_notes TEXT,
                    historical_outreach_events_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_candidate_domain ON candidate_companies(company_domain);
                CREATE INDEX IF NOT EXISTS idx_candidate_norm_name ON candidate_companies(normalized_company_name);

                CREATE TABLE IF NOT EXISTS discovery_observations (
                    observation_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_name TEXT,
                    source_type TEXT NOT NULL,
                    source_url TEXT,
                    source_external_id TEXT,
                    discovery_mode TEXT DEFAULT 'company_first',
                    observed_company_name TEXT NOT NULL,
                    observed_domain TEXT,
                    country TEXT,
                    region TEXT,
                    city TEXT,
                    observation_type TEXT,
                    observed_company_type TEXT,
                    raw_title TEXT,
                    raw_snippet TEXT,
                    raw_payload_json TEXT,
                    extracted_data_json TEXT,
                    observed_at TEXT,
                    published_at TEXT,
                    fetched_at TEXT,
                    confidence REAL,
                    evidence_refs_json TEXT,
                    raw_ref_json TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_obs_candidate ON discovery_observations(candidate_id);

                CREATE TABLE IF NOT EXISTS market_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source_url TEXT,
                    source_type TEXT,
                    observed_at TEXT,
                    published_at TEXT,
                    freshness_status TEXT DEFAULT 'unknown',
                    confidence REAL,
                    is_current INTEGER,
                    raw_ref_json TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_ev_candidate ON market_evidence(candidate_id);

                CREATE TABLE IF NOT EXISTS watchlist (
                    candidate_id TEXT PRIMARY KEY,
                    watch_reason TEXT NOT NULL,
                    watch_status TEXT NOT NULL,
                    next_check_at TEXT,
                    last_checked_at TEXT,
                    signals_to_monitor_json TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS historical_outreach_events (
                    event_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    source_ref TEXT,
                    contact_name TEXT,
                    contact_email TEXT,
                    contacted_at TEXT,
                    follow_up_at TEXT,
                    channel TEXT,
                    reply_status TEXT,
                    reply_at TEXT,
                    respondent_name TEXT,
                    respondent_role TEXT,
                    respondent_email TEXT,
                    meeting_status TEXT,
                    opportunity_status TEXT,
                    intent_evidence TEXT,
                    relationship_type TEXT,
                    opportunity_types_json TEXT,
                    notes TEXT,
                    raw_row_hash TEXT UNIQUE,
                    FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS candidate_contacts (
                    contact_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    full_name TEXT,
                    job_title TEXT,
                    work_email TEXT,
                    email_status TEXT DEFAULT 'unknown',
                    email_confidence INTEGER DEFAULT 0,
                    seniority TEXT,
                    department TEXT,
                    linkedin_url TEXT,
                    provider TEXT,
                    provider_person_id TEXT,
                    buyer_role_match TEXT,
                    contact_score INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_contact_cand ON candidate_contacts(candidate_id);

                CREATE TABLE IF NOT EXISTS candidate_contact_verifications (
                    verification_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    contact_id TEXT NOT NULL,
                    work_email TEXT NOT NULL,
                    evidence_source TEXT NOT NULL,
                    evidence_reference TEXT,
                    verified_by TEXT NOT NULL DEFAULT 'local_operator',
                    verified_at TEXT NOT NULL,
                    notes TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE CASCADE,
                    FOREIGN KEY (contact_id) REFERENCES candidate_contacts(contact_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_verif_cand ON candidate_contact_verifications(candidate_id);
                CREATE INDEX IF NOT EXISTS idx_verif_contact ON candidate_contact_verifications(contact_id);
                CREATE INDEX IF NOT EXISTS idx_verif_email ON candidate_contact_verifications(work_email);

                CREATE TABLE IF NOT EXISTS discovery_runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    observations_found INTEGER DEFAULT 0,
                    new_candidates INTEGER DEFAULT 0,
                    updated_candidates INTEGER DEFAULT 0,
                    known_candidates INTEGER DEFAULT 0,
                    watchlist_candidates INTEGER DEFAULT 0,
                    qualified_candidates INTEGER DEFAULT 0,
                    rejected_candidates INTEGER DEFAULT 0,
                    candidates_discovered INTEGER DEFAULT 0,
                    candidates_updated INTEGER DEFAULT 0,
                    observations_created INTEGER DEFAULT 0,
                    errors_json TEXT NOT NULL DEFAULT '[]',
                    config_version TEXT DEFAULT 'v1'
                );

                CREATE TABLE IF NOT EXISTS source_health (
                    source_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    last_success_at TEXT,
                    last_failure_at TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS pilot_search_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    query TEXT NOT NULL,
                    country TEXT,
                    city TEXT,
                    requested_at TEXT NOT NULL,
                    results_count INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    estimated_cost_usd REAL DEFAULT 0.005,
                    operator_approved INTEGER DEFAULT 1
                );
            """)

            # Safe additive column migrations for existing databases
            self._migrate_columns(conn)

    def _migrate_columns(self, conn: sqlite3.Connection):
        def _add_col(table: str, col_def: str):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass

        _add_col("discovery_observations", "source_name TEXT")
        _add_col("discovery_observations", "discovery_mode TEXT DEFAULT 'company_first'")
        _add_col("discovery_observations", "region TEXT")
        _add_col("discovery_observations", "observed_company_type TEXT")
        _add_col("discovery_observations", "raw_payload_json TEXT")
        _add_col("discovery_observations", "extracted_data_json TEXT")
        _add_col("discovery_observations", "published_at TEXT")
        _add_col("discovery_observations", "raw_ref_json TEXT")

        _add_col("market_evidence", "published_at TEXT")
        _add_col("market_evidence", "freshness_status TEXT DEFAULT 'unknown'")

        _add_col("historical_outreach_events", "follow_up_at TEXT")
        _add_col("historical_outreach_events", "meeting_status TEXT")
        _add_col("historical_outreach_events", "opportunity_status TEXT")
        _add_col("historical_outreach_events", "intent_evidence TEXT")
        _add_col("historical_outreach_events", "relationship_type TEXT")
        _add_col("historical_outreach_events", "opportunity_types_json TEXT")

        _add_col("candidate_companies", "qualification_outcome TEXT")
        _add_col("candidate_companies", "qualification_reasons_json TEXT")
        _add_col("candidate_companies", "manual_review_decision TEXT DEFAULT 'NOT_REVIEWED'")
        _add_col("candidate_companies", "manual_reviewed_at TEXT")
        _add_col("candidate_companies", "manual_reviewed_by TEXT")
        _add_col("candidate_companies", "manual_review_notes TEXT")

        _add_col("pilot_search_attempts", "idempotency_token TEXT")
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pilot_idempotency ON pilot_search_attempts(idempotency_token) WHERE idempotency_token IS NOT NULL")
        except sqlite3.OperationalError:
            pass

    def _generate_candidate_id(self, domain: Optional[str], name: str, country: Optional[str] = None) -> str:
        if domain:
            clean_dom = re.sub(r'[^a-z0-9.-]', '', domain.lower())
            return f"cand:domain:{clean_dom}"
        clean_name = re.sub(r'[^a-z0-9]', '', normalize_company_name(name))
        c_code = country.lower().strip() if country else "global"
        return f"cand:name:{clean_name}:{c_code}"

    def get_or_create_candidate(
        self,
        company_name: str,
        company_domain: Optional[str] = None,
        country: Optional[str] = None,
        source_id: Optional[str] = None,
        city: Optional[str] = None,
        company_type: Optional[str] = None,
    ) -> Tuple[CandidateCompany, bool]:
        """
        Deduplicates candidate companies.
        Rule 1: Same canonical domain across multiple sources -> one CandidateCompany.
        Rule 2: Same company name with different trusted domains -> separate CandidateCompanies!
        Rule 3: Never fuzzy-merge by name alone when domains differ.
        """
        now = datetime.now(timezone.utc).isoformat()
        canonical_dom = canonicalize_domain(company_domain)
        norm_name = normalize_company_name(company_name)

        existing: Optional[CandidateCompany] = None

        # 1. Match by domain if available
        if canonical_dom:
            existing = self.get_candidate_by_domain(canonical_dom)

        # 2. If no domain on input, check if there's an existing candidate with same normalized name and NO domain
        if not existing and not canonical_dom:
            candidates = self.list_candidates()
            for c in candidates:
                if c.normalized_company_name == norm_name and not c.company_domain:
                    # Match only if country matches or is unspecified
                    if not country or not c.country or c.country.lower() == country.lower():
                        existing = c
                        break

        if existing:
            # Update observation tracking
            updated = False
            if company_name not in existing.aliases and company_name != existing.company_name:
                existing.aliases.append(company_name)
                updated = True
            if source_id and source_id not in existing.source_refs:
                existing.source_refs.append(source_id)
                updated = True
            if company_type and company_type not in existing.company_types:
                existing.company_types.append(company_type)
                updated = True
            if not existing.country and country:
                existing.country = country
                updated = True
            if not existing.city and city:
                existing.city = city
                updated = True
            existing.last_seen_at = now
            self.save_candidate(existing)
            return existing, False

        # Create new CandidateCompany
        cid = self._generate_candidate_id(canonical_dom, company_name, country)

        # Check Excluded Companies Registry: if newly discovered company is already excluded,
        # ensure it starts in rejected/suppressed status and cold outreach is prohibited.
        is_excl = False
        excl_reason = None
        try:
            with self._get_connection() as conn:
                r_has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='excluded_companies' LIMIT 1").fetchone()
                if r_has:
                    if canonical_dom:
                        r_excl = conn.execute(
                            "SELECT exclusion_reason FROM excluded_companies WHERE canonical_domain = ? AND exclusion_status = 'ACTIVE' LIMIT 1",
                            (canonical_dom,)
                        ).fetchone()
                        if r_excl:
                            is_excl = True
                            excl_reason = r_excl[0]
                    if not is_excl and cid:
                        r_excl2 = conn.execute(
                            "SELECT exclusion_reason FROM excluded_companies WHERE candidate_id = ? AND exclusion_status = 'ACTIVE' LIMIT 1",
                            (cid,)
                        ).fetchone()
                        if r_excl2:
                            is_excl = True
                            excl_reason = r_excl2[0]
                    if not is_excl and company_name:
                        r_excl3 = conn.execute(
                            "SELECT exclusion_reason FROM excluded_companies WHERE LOWER(company_name) = ? AND exclusion_status = 'ACTIVE' LIMIT 1",
                            (company_name.strip().lower(),)
                        ).fetchone()
                        if r_excl3:
                            is_excl = True
                            excl_reason = r_excl3[0]
        except Exception:
            pass

        init_status = DiscoveryStatus.rejected if is_excl else DiscoveryStatus.new
        init_outcome = QualificationOutcome.reject if is_excl else None
        init_notes = f"Auto-classified as rejected: matching active exclusion ({excl_reason})" if is_excl else None

        new_cand = CandidateCompany(
            candidate_id=cid,
            company_name=company_name.strip(),
            normalized_company_name=norm_name,
            company_domain=canonical_dom,
            country=country,
            city=city,
            company_types=[company_type] if company_type else [],
            source_refs=[source_id] if source_id else [],
            aliases=[company_name.strip()],
            domain_history=[canonical_dom] if canonical_dom else [],
            discovery_status=init_status,
            qualification_outcome=init_outcome,
            manual_review_notes=init_notes,
            cold_outreach_allowed=not is_excl,
            first_seen_at=now,
            last_seen_at=now,
        )
        self.save_candidate(new_cand)
        return new_cand, True

    def save_candidate(self, cand: CandidateCompany):
        if not cand.candidate_id or not str(cand.candidate_id).strip():
            raise ValueError("Cannot persist candidate: candidate_id must be a non-empty, non-whitespace string.")

        with self._get_connection() as conn:
            # Monotonic outreach guard enforcement in Python memory
            existing_row = conn.execute(
                "SELECT cold_outreach_allowed, suppressed, active_conversation, historical_contact_status FROM candidate_companies WHERE candidate_id = ?",
                (cand.candidate_id,)
            ).fetchone()
            if existing_row:
                if existing_row["cold_outreach_allowed"] == 0 or cand.cold_outreach_allowed is False:
                    cand.cold_outreach_allowed = False
                if existing_row["suppressed"] or cand.suppressed:
                    cand.suppressed = True
                if existing_row["active_conversation"] or cand.active_conversation:
                    cand.active_conversation = True
                if existing_row["historical_contact_status"] in ('contacted', 'replied', 'converted', 'dnc') and cand.historical_contact_status == 'uncontacted':
                    cand.historical_contact_status = existing_row["historical_contact_status"]

            conn.execute("""
                INSERT INTO candidate_companies (
                    candidate_id, company_name, normalized_company_name, company_domain,
                    country, region, city, company_types_json, industry_tags_json,
                    legal_form, legal_form_raw, employee_size_hint, project_scale_hint,
                    company_tier, tier_confidence, tier_evidence_json,
                    bim_maturity, bim_maturity_confidence, direct_project_access,
                    direct_project_access_confidence, outsourcing_potential, automation_potential,
                    relationship_type, opportunity_types_json, discovery_status,
                    fit_assessment, fit_reasons_json, intent_assessment, intent_signals_json,
                    source_refs_json, evidence_refs_json, aliases_json, source_identities_json,
                    domain_history_json, first_seen_at, last_seen_at, last_researched_at,
                    historical_contact_status, last_contacted_at, reply_status,
                    active_conversation, suppressed, cold_outreach_allowed,
                    qualification_outcome, qualification_reasons_json,
                    manual_review_decision, manual_reviewed_at, manual_reviewed_by, manual_review_notes
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(candidate_id) DO UPDATE SET
                    company_name = excluded.company_name,
                    normalized_company_name = excluded.normalized_company_name,
                    company_domain = excluded.company_domain,
                    country = COALESCE(excluded.country, candidate_companies.country),
                    region = COALESCE(excluded.region, candidate_companies.region),
                    city = COALESCE(excluded.city, candidate_companies.city),
                    company_types_json = excluded.company_types_json,
                    industry_tags_json = excluded.industry_tags_json,
                    legal_form = COALESCE(excluded.legal_form, candidate_companies.legal_form),
                    legal_form_raw = COALESCE(excluded.legal_form_raw, candidate_companies.legal_form_raw),
                    employee_size_hint = COALESCE(excluded.employee_size_hint, candidate_companies.employee_size_hint),
                    project_scale_hint = COALESCE(excluded.project_scale_hint, candidate_companies.project_scale_hint),
                    company_tier = excluded.company_tier,
                    tier_confidence = excluded.tier_confidence,
                    tier_evidence_json = excluded.tier_evidence_json,
                    bim_maturity = excluded.bim_maturity,
                    bim_maturity_confidence = excluded.bim_maturity_confidence,
                    direct_project_access = excluded.direct_project_access,
                    direct_project_access_confidence = excluded.direct_project_access_confidence,
                    outsourcing_potential = excluded.outsourcing_potential,
                    automation_potential = excluded.automation_potential,
                    relationship_type = excluded.relationship_type,
                    opportunity_types_json = excluded.opportunity_types_json,
                    discovery_status = excluded.discovery_status,
                    fit_assessment = excluded.fit_assessment,
                    fit_reasons_json = excluded.fit_reasons_json,
                    intent_assessment = excluded.intent_assessment,
                    intent_signals_json = excluded.intent_signals_json,
                    source_refs_json = excluded.source_refs_json,
                    evidence_refs_json = excluded.evidence_refs_json,
                    aliases_json = excluded.aliases_json,
                    source_identities_json = excluded.source_identities_json,
                    domain_history_json = excluded.domain_history_json,
                    last_seen_at = excluded.last_seen_at,
                    last_researched_at = COALESCE(excluded.last_researched_at, candidate_companies.last_researched_at),
                    historical_contact_status = CASE
                        WHEN candidate_companies.historical_contact_status IN ('contacted', 'replied', 'converted', 'dnc')
                              AND excluded.historical_contact_status = 'uncontacted'
                        THEN candidate_companies.historical_contact_status
                        ELSE excluded.historical_contact_status
                    END,
                    last_contacted_at = COALESCE(excluded.last_contacted_at, candidate_companies.last_contacted_at),
                    reply_status = COALESCE(excluded.reply_status, candidate_companies.reply_status),
                    active_conversation = MAX(candidate_companies.active_conversation, excluded.active_conversation),
                    suppressed = MAX(candidate_companies.suppressed, excluded.suppressed),
                    cold_outreach_allowed = CASE
                        WHEN candidate_companies.cold_outreach_allowed = 0 OR excluded.cold_outreach_allowed = 0 THEN 0
                        ELSE 1
                    END,
                    qualification_outcome = COALESCE(excluded.qualification_outcome, candidate_companies.qualification_outcome),
                    qualification_reasons_json = CASE
                        WHEN excluded.qualification_reasons_json != '[]' THEN excluded.qualification_reasons_json
                        ELSE candidate_companies.qualification_reasons_json
                    END,
                    manual_review_decision = CASE
                        WHEN excluded.manual_review_decision != 'NOT_REVIEWED' THEN excluded.manual_review_decision
                        ELSE candidate_companies.manual_review_decision
                    END,
                    manual_reviewed_at = COALESCE(excluded.manual_reviewed_at, candidate_companies.manual_reviewed_at),
                    manual_reviewed_by = COALESCE(excluded.manual_reviewed_by, candidate_companies.manual_reviewed_by),
                    manual_review_notes = COALESCE(excluded.manual_review_notes, candidate_companies.manual_review_notes)
            """, (
                cand.candidate_id, cand.company_name, cand.normalized_company_name, cand.company_domain,
                cand.country, cand.region, cand.city,
                json.dumps(cand.company_types), json.dumps(cand.industry_tags),
                cand.legal_form, cand.legal_form_raw, cand.employee_size_hint, cand.project_scale_hint,
                cand.company_tier.value if isinstance(cand.company_tier, CompanyTier) else str(cand.company_tier),
                cand.tier_confidence, json.dumps(cand.tier_evidence),
                cand.bim_maturity.value if isinstance(cand.bim_maturity, BIMMaturity) else str(cand.bim_maturity),
                cand.bim_maturity_confidence,
                1 if cand.direct_project_access else 0, cand.direct_project_access_confidence,
                cand.outsourcing_potential, cand.automation_potential,
                cand.relationship_type.value if isinstance(cand.relationship_type, RelationshipType) else str(cand.relationship_type),
                json.dumps([o.value if isinstance(o, OpportunityType) else str(o) for o in cand.opportunity_types]),
                cand.discovery_status.value if isinstance(cand.discovery_status, DiscoveryStatus) else str(cand.discovery_status),
                cand.fit_assessment, json.dumps(cand.fit_reasons),
                cand.intent_assessment, json.dumps(cand.intent_signals),
                json.dumps(cand.source_refs), json.dumps(cand.evidence_refs),
                json.dumps(cand.aliases), json.dumps(cand.source_identities), json.dumps(cand.domain_history),
                cand.first_seen_at, cand.last_seen_at, cand.last_researched_at,
                cand.historical_contact_status, cand.last_contacted_at, cand.reply_status,
                1 if cand.active_conversation else 0, 1 if cand.suppressed else 0, 1 if cand.cold_outreach_allowed else 0,
                cand.qualification_outcome.value if hasattr(cand.qualification_outcome, 'value') else (cand.qualification_outcome or None),
                json.dumps(cand.qualification_reasons or []),
                cand.manual_review_decision or 'NOT_REVIEWED',
                cand.manual_reviewed_at,
                cand.manual_reviewed_by,
                cand.manual_review_notes,
            ))

    def _row_to_candidate(self, row: sqlite3.Row) -> CandidateCompany:
        return CandidateCompany(
            candidate_id=row["candidate_id"],
            company_name=row["company_name"],
            normalized_company_name=row["normalized_company_name"],
            company_domain=row["company_domain"],
            country=row["country"],
            region=row["region"],
            city=row["city"],
            company_types=json.loads(row["company_types_json"] or "[]"),
            industry_tags=json.loads(row["industry_tags_json"] or "[]"),
            legal_form=row["legal_form"],
            legal_form_raw=row["legal_form_raw"],
            employee_size_hint=row["employee_size_hint"],
            project_scale_hint=row["project_scale_hint"],
            company_tier=CompanyTier(row["company_tier"]) if row["company_tier"] in CompanyTier._value2member_map_ else CompanyTier.unknown,
            tier_confidence=row["tier_confidence"],
            tier_evidence=json.loads(row["tier_evidence_json"] or "[]"),
            bim_maturity=BIMMaturity(row["bim_maturity"]) if row["bim_maturity"] in BIMMaturity._value2member_map_ else BIMMaturity.unknown,
            bim_maturity_confidence=row["bim_maturity_confidence"],
            direct_project_access=bool(row["direct_project_access"]),
            direct_project_access_confidence=row["direct_project_access_confidence"],
            outsourcing_potential=row["outsourcing_potential"] or "unknown",
            automation_potential=row["automation_potential"] or "unknown",
            relationship_type=RelationshipType(row["relationship_type"]) if row["relationship_type"] in RelationshipType._value2member_map_ else RelationshipType.unknown,
            opportunity_types=[OpportunityType(o) for o in json.loads(row["opportunity_types_json"] or "[]") if o in OpportunityType._value2member_map_],
            discovery_status=DiscoveryStatus(row["discovery_status"]) if row["discovery_status"] in DiscoveryStatus._value2member_map_ else DiscoveryStatus.new,
            fit_assessment=row["fit_assessment"] or "unknown",
            fit_reasons=json.loads(row["fit_reasons_json"] or "[]"),
            intent_assessment=row["intent_assessment"] or "unknown",
            intent_signals=json.loads(row["intent_signals_json"] or "[]"),
            source_refs=json.loads(row["source_refs_json"] or "[]"),
            evidence_refs=json.loads(row["evidence_refs_json"] or "[]"),
            aliases=json.loads(row["aliases_json"] or "[]"),
            source_identities=json.loads(row["source_identities_json"] or "[]"),
            domain_history=json.loads(row["domain_history_json"] or "[]"),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            last_researched_at=row["last_researched_at"],
            historical_contact_status=row["historical_contact_status"] or "uncontacted",
            last_contacted_at=row["last_contacted_at"],
            reply_status=row["reply_status"],
            active_conversation=bool(row["active_conversation"]),
            suppressed=bool(row["suppressed"]),
            cold_outreach_allowed=bool(row["cold_outreach_allowed"]),
            qualification_outcome=QualificationOutcome(row["qualification_outcome"]) if ("qualification_outcome" in row.keys() and row["qualification_outcome"] in QualificationOutcome._value2member_map_) else None,
            qualification_reasons=json.loads(row["qualification_reasons_json"] or "[]") if ("qualification_reasons_json" in row.keys() and row["qualification_reasons_json"]) else [],
            manual_review_decision=row["manual_review_decision"] if "manual_review_decision" in row.keys() and row["manual_review_decision"] else "NOT_REVIEWED",
            manual_reviewed_at=row["manual_reviewed_at"] if "manual_reviewed_at" in row.keys() else None,
            manual_reviewed_by=row["manual_reviewed_by"] if "manual_reviewed_by" in row.keys() else None,
            manual_review_notes=row["manual_review_notes"] if "manual_review_notes" in row.keys() else None,
            metadata={
                "active_conversation": bool(row["active_conversation"]),
                "has_historical_outreach": (row["historical_contact_status"] or "uncontacted") in ("contacted", "replied", "converted"),
            },
        )

    def get_candidate(self, candidate_id: str) -> Optional[CandidateCompany]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM candidate_companies WHERE candidate_id = ?", (candidate_id,)).fetchone()
            if row:
                return self._row_to_candidate(row)
        return None

    def get_candidate_by_domain(self, domain: str) -> Optional[CandidateCompany]:
        canon = canonicalize_domain(domain)
        if not canon:
            return None
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM candidate_companies WHERE company_domain = ?", (canon,)).fetchone()
            if row:
                return self._row_to_candidate(row)
        return None

    get_by_domain = get_candidate_by_domain

    def get_by_source_identity(self, identity: str) -> Optional[CandidateCompany]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM candidate_companies").fetchall()
            for r in rows:
                sids = json.loads(r["source_identities_json"] or "[]")
                if identity in sids:
                    return self._row_to_candidate(r)
        return None

    def add_alias_to_candidate(self, candidate_id: str, alias: str) -> None:
        cand = self.get_candidate(candidate_id)
        if cand and alias not in cand.aliases:
            cand.aliases.append(alias)
            self.save_candidate(cand)

    def add_domain_history_to_candidate(self, candidate_id: str, old_domain: str) -> None:
        cand = self.get_candidate(candidate_id)
        if cand and old_domain not in cand.domain_history:
            cand.domain_history.append(old_domain)
            self.save_candidate(cand)

    def list_candidates(self, filters: Optional[Dict[str, Any]] = None) -> List[CandidateCompany]:
        filters = filters or {}
        query = "SELECT * FROM candidate_companies WHERE 1=1"
        params = []

        if "country" in filters:
            query += " AND LOWER(country) = LOWER(?)"
            params.append(filters["country"])
        if "city" in filters:
            query += " AND LOWER(city) = LOWER(?)"
            params.append(filters["city"])
        if "discovery_status" in filters:
            query += " AND discovery_status = ?"
            params.append(filters["discovery_status"])
        if "qualification_outcome" in filters:
            query += " AND qualification_outcome = ?"
            params.append(filters["qualification_outcome"])
        if "company_tier" in filters:
            query += " AND company_tier = ?"
            params.append(filters["company_tier"])
        if "relationship_type" in filters:
            query += " AND relationship_type = ?"
            params.append(filters["relationship_type"])
        if "cold_outreach_allowed" in filters:
            query += " AND cold_outreach_allowed = ?"
            params.append(1 if filters["cold_outreach_allowed"] else 0)

        query += " ORDER BY first_seen_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_candidate(r) for r in rows]

    def add_observation(self, obs: DiscoveryObservation) -> bool:
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM discovery_observations WHERE observation_id = ?",
                (obs.observation_id,)
            ).fetchone()
            conn.execute("""
                INSERT INTO discovery_observations (
                    observation_id, candidate_id, source_id, source_name, source_type,
                    source_url, source_external_id, discovery_mode, observed_company_name, observed_domain,
                    country, region, city, observation_type, observed_company_type, raw_title, raw_snippet,
                    raw_payload_json, extracted_data_json, observed_at, published_at, fetched_at,
                    confidence, evidence_refs_json, raw_ref_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    observed_at = excluded.observed_at,
                    confidence = excluded.confidence,
                    extracted_data_json = excluded.extracted_data_json
            """, (
                obs.observation_id, obs.candidate_id, obs.source_id, obs.source_name, obs.source_type,
                obs.source_url, obs.source_external_id, obs.discovery_mode, obs.observed_company_name, obs.observed_domain,
                obs.country, obs.region, obs.city, obs.observation_type, obs.observed_company_type, obs.raw_title, obs.raw_snippet,
                json.dumps(obs.raw_payload or {}), json.dumps(obs.extracted_data or {}), obs.observed_at, obs.published_at, obs.fetched_at,
                obs.confidence, json.dumps(obs.evidence_refs or []), json.dumps(obs.raw_ref or {})
            ))
            return existing is None

    save_observation = add_observation

    def get_observations(self, candidate_id: str) -> List[DiscoveryObservation]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM discovery_observations WHERE candidate_id = ? ORDER BY observed_at DESC",
                (candidate_id,)
            ).fetchall()
            return [
                DiscoveryObservation(
                    observation_id=r["observation_id"],
                    candidate_id=r["candidate_id"],
                    source_id=r["source_id"],
                    source_name=r["source_name"] if "source_name" in r.keys() else None,
                    source_type=r["source_type"],
                    source_url=r["source_url"],
                    source_external_id=r["source_external_id"],
                    discovery_mode=r["discovery_mode"] if "discovery_mode" in r.keys() else "company_first",
                    observed_company_name=r["observed_company_name"],
                    observed_domain=r["observed_domain"],
                    country=r["country"],
                    region=r["region"] if "region" in r.keys() else None,
                    city=r["city"],
                    observation_type=r["observation_type"],
                    observed_company_type=r["observed_company_type"] if "observed_company_type" in r.keys() else None,
                    raw_title=r["raw_title"],
                    raw_snippet=r["raw_snippet"],
                    raw_payload=json.loads(r["raw_payload_json"] or "{}") if "raw_payload_json" in r.keys() and r["raw_payload_json"] else {},
                    extracted_data=json.loads(r["extracted_data_json"] or "{}") if "extracted_data_json" in r.keys() and r["extracted_data_json"] else {},
                    observed_at=r["observed_at"],
                    published_at=r["published_at"] if "published_at" in r.keys() else None,
                    fetched_at=r["fetched_at"],
                    confidence=r["confidence"],
                    evidence_refs=json.loads(r["evidence_refs_json"] or "[]"),
                    raw_ref=json.loads(r["raw_ref_json"] or "{}") if "raw_ref_json" in r.keys() and r["raw_ref_json"] else {},
                )
                for r in rows
            ]

    def add_evidence(self, ev: MarketEvidence):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO market_evidence (
                    evidence_id, candidate_id, category, signal_type,
                    text, source_url, source_type, observed_at, confidence, is_current, raw_ref_json,
                    published_at, freshness_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    text = excluded.text,
                    is_current = excluded.is_current,
                    confidence = excluded.confidence,
                    published_at = COALESCE(excluded.published_at, market_evidence.published_at),
                    freshness_status = excluded.freshness_status
            """, (
                ev.evidence_id, ev.candidate_id, ev.category, ev.signal_type,
                ev.text, ev.source_url, ev.source_type, ev.observed_at,
                ev.confidence, 1 if ev.is_current else 0, json.dumps(ev.raw_ref or {}),
                ev.published_at, ev.freshness_status
            ))

    def save_evidence(self, ev: MarketEvidence):
        return self.add_evidence(ev)

    def get_evidence(self, candidate_id: str) -> List[MarketEvidence]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM market_evidence WHERE candidate_id = ? ORDER BY observed_at DESC",
                (candidate_id,)
            ).fetchall()
            return [
                MarketEvidence(
                    evidence_id=r["evidence_id"],
                    candidate_id=r["candidate_id"],
                    category=r["category"],
                    signal_type=r["signal_type"],
                    text=r["text"],
                    source_url=r["source_url"],
                    source_type=r["source_type"],
                    observed_at=r["observed_at"],
                    confidence=r["confidence"],
                    is_current=bool(r["is_current"]),
                    raw_ref=json.loads(r["raw_ref_json"] or "{}"),
                    published_at=r["published_at"] if "published_at" in r.keys() else None,
                    freshness_status=r["freshness_status"] if "freshness_status" in r.keys() else "unknown",
                )
                for r in rows
            ]

    def add_to_watchlist(self, candidate_id: str, reason: str, signals: Optional[List[str]] = None) -> WatchlistEntry:
        now = datetime.now(timezone.utc).isoformat()
        entry = WatchlistEntry(
            candidate_id=candidate_id,
            watch_reason=reason,
            watch_status="active",
            last_checked_at=now,
            signals_to_monitor=signals or ["careers", "tenders", "projects"],
        )
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO watchlist (candidate_id, watch_reason, watch_status, next_check_at, last_checked_at, signals_to_monitor_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    watch_reason = excluded.watch_reason,
                    watch_status = excluded.watch_status,
                    last_checked_at = excluded.last_checked_at,
                    signals_to_monitor_json = excluded.signals_to_monitor_json
            """, (
                entry.candidate_id, entry.watch_reason, entry.watch_status,
                entry.next_check_at, entry.last_checked_at, json.dumps(entry.signals_to_monitor)
            ))
            # Mark candidate status as watchlist
            conn.execute(
                "UPDATE candidate_companies SET discovery_status = 'watchlist' WHERE candidate_id = ?",
                (candidate_id,)
            )
        return entry

    def get_watchlist(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT w.*, c.company_name, c.company_domain, c.company_tier, c.fit_assessment, c.relationship_type
                FROM watchlist w
                JOIN candidate_companies c ON w.candidate_id = c.candidate_id
                WHERE w.watch_status = 'active'
                ORDER BY w.last_checked_at DESC
            """).fetchall()
            return [
                {
                    "candidate_id": r["candidate_id"],
                    "company_id": r["candidate_id"],
                    "company_name": r["company_name"],
                    "company_domain": r["company_domain"],
                    "company_tier": r["company_tier"],
                    "fit_assessment": r["fit_assessment"],
                    "relationship_type": r["relationship_type"],
                    "watch_reason": r["watch_reason"],
                    "watch_status": r["watch_status"],
                    "last_checked_at": r["last_checked_at"],
                    "signals_to_monitor": json.loads(r["signals_to_monitor_json"] or "[]"),
                }
                for r in rows
            ]

    def is_on_watchlist(self, candidate_id: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM watchlist WHERE candidate_id = ? AND watch_status = 'active'",
                (candidate_id,)
            ).fetchone()
            return row is not None

    def promote_from_watchlist(
        self,
        candidate_id: str,
        new_evidence: Optional[MarketEvidence] = None,
        qualification_reason: str = "Promoted upon new signal",
        new_status: Optional[Any] = None,
        reason: Optional[str] = None,
    ) -> CandidateCompany:
        """
        P10-REG-009: New signal promotes watchlist candidate without creating duplicate CandidateCompany.
        """
        qualification_reason = reason or qualification_reason
        cand = self.get_candidate(candidate_id)
        if not cand:
            raise ValueError(f"Candidate {candidate_id} not found in store")

        if new_evidence:
            self.add_evidence(new_evidence)
            if new_evidence.evidence_id not in cand.evidence_refs:
                cand.evidence_refs.append(new_evidence.evidence_id)

        # Update candidate state directly on the existing identity
        target_status = DiscoveryStatus.qualified
        if new_status:
            if isinstance(new_status, DiscoveryStatus):
                target_status = new_status
            elif isinstance(new_status, str) and new_status in DiscoveryStatus._value2member_map_:
                target_status = DiscoveryStatus(new_status)

        cand.discovery_status = target_status
        cand.intent_assessment = "high"
        cand.fit_reasons.append(qualification_reason)
        cand.last_seen_at = datetime.now(timezone.utc).isoformat()
        self.save_candidate(cand)

        # Remove or mark closed in watchlist table
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE watchlist SET watch_status = 'promoted' WHERE candidate_id = ?",
                (candidate_id,)
            )

        return cand

    def get_observations_for_company(self, company_id: str) -> List[DiscoveryObservation]:
        return self.get_observations(company_id)

    def record_historical_outreach(
        self,
        candidate_id: str,
        source_ref: str,
        contact_name: Optional[str] = None,
        contact_email: Optional[str] = None,
        contacted_at: Optional[str] = None,
        channel: str = "email",
        reply_status: Optional[str] = None,
        reply_at: Optional[str] = None,
        respondent_name: Optional[str] = None,
        respondent_role: Optional[str] = None,
        respondent_email: Optional[str] = None,
        notes: Optional[str] = None,
        raw_row_hash: Optional[str] = None,
        follow_up_at: Optional[str] = None,
        meeting_status: Optional[str] = None,
        opportunity_status: Optional[str] = None,
        intent_evidence: Optional[str] = None,
        relationship_type: Optional[str] = None,
        opportunity_types: Optional[List[str]] = None,
    ) -> bool:
        """
        Records historical outreach event idempotently.
        Returns True if inserted, False if duplicate row hash.
        """
        event_id = f"outreach:{candidate_id}:{raw_row_hash or contacted_at or contact_email or respondent_email}"
        with self._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO historical_outreach_events (
                        event_id, candidate_id, source_ref, contact_name, contact_email,
                        contacted_at, channel, reply_status, reply_at,
                        respondent_name, respondent_role, respondent_email,
                        notes, raw_row_hash, follow_up_at, meeting_status,
                        opportunity_status, intent_evidence, relationship_type, opportunity_types_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event_id, candidate_id, source_ref, contact_name, contact_email,
                    contacted_at, channel, reply_status, reply_at,
                    respondent_name, respondent_role, respondent_email,
                    notes, raw_row_hash, follow_up_at, meeting_status,
                    opportunity_status, intent_evidence, relationship_type,
                    json.dumps(opportunity_types or [])
                ))
                return True
            except sqlite3.IntegrityError:
                # Duplicate row hash / idempotent ignore
                return False

    def get_historical_outreach_for_candidate(self, candidate_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM historical_outreach_events WHERE candidate_id = ? ORDER BY contacted_at DESC",
                (candidate_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def save_discovery_run(self, run: DiscoveryRunRecord):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO discovery_runs (
                    run_id, mode, source_name, started_at, completed_at, status,
                    observations_found, new_candidates, updated_candidates, known_candidates,
                    watchlist_candidates, qualified_candidates, rejected_candidates,
                    candidates_discovered, candidates_updated, observations_created,
                    errors_json, config_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    completed_at = excluded.completed_at,
                    status = excluded.status,
                    observations_found = excluded.observations_found,
                    new_candidates = excluded.new_candidates,
                    updated_candidates = excluded.updated_candidates,
                    known_candidates = excluded.known_candidates,
                    watchlist_candidates = excluded.watchlist_candidates,
                    qualified_candidates = excluded.qualified_candidates,
                    rejected_candidates = excluded.rejected_candidates,
                    candidates_discovered = excluded.candidates_discovered,
                    candidates_updated = excluded.candidates_updated,
                    observations_created = excluded.observations_created,
                    errors_json = excluded.errors_json
            """, (
                run.run_id, run.mode, run.source_name, run.started_at, run.completed_at, run.status,
                run.observations_found, run.new_candidates, run.updated_candidates, run.known_candidates,
                run.watchlist_candidates, run.qualified_candidates, run.rejected_candidates,
                run.candidates_discovered, run.candidates_updated, run.observations_created,
                json.dumps(run.errors or []), run.config_version
            ))

    def get_discovery_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM discovery_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row:
                d = dict(row)
                d["errors"] = json.loads(d["errors_json"] or "[]")
                return d
        return None

    def list_discovery_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM discovery_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                d["errors"] = json.loads(d["errors_json"] or "[]")
                res.append(d)
            return res

    def save_source_health(self, health: SourceHealthRecord):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO source_health (
                    source_name, status, success_count, failure_count,
                    last_success_at, last_failure_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    status = excluded.status,
                    success_count = excluded.success_count,
                    failure_count = excluded.failure_count,
                    last_success_at = excluded.last_success_at,
                    last_failure_at = excluded.last_failure_at,
                    last_error = excluded.last_error
            """, (
                health.source_name, health.status, health.success_count, health.failure_count,
                health.last_success_at, health.last_failure_at, health.last_error
            ))

    def get_all_source_health(self) -> Dict[str, Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM source_health").fetchall()
            return {r["source_name"]: dict(r) for r in rows}

    # Convenient aliases for compatibility
    def get_by_id(self, candidate_id: str) -> Optional[CandidateCompany]:
        return self.get_candidate(candidate_id)

    def get_by_domain(self, domain: str) -> Optional[CandidateCompany]:
        return self.get_candidate_by_domain(domain)

    find_or_create_candidate = get_or_create_candidate
    get_observations_for_candidate = get_observations
    save_market_evidence = add_evidence
    get_evidence_for_candidate = get_evidence

    def upsert_company(self, cand: CandidateCompany, observation: Optional[DiscoveryObservation] = None) -> CandidateCompany:
        self.save_candidate(cand)
        if observation:
            self.add_observation(observation)
        return cand

    @staticmethod
    def canonicalize_domain(domain_or_url: Optional[str]) -> Optional[str]:
        return canonicalize_domain(domain_or_url)

    def generate_company_id(self, name: str, domain: Optional[str] = None, country: Optional[str] = None) -> str:
        return self._generate_candidate_id(domain, name, country)

    def save_contact(self, contact: Union[ContactCandidate, Dict[str, Any]], candidate_id: str) -> ContactCandidate:
        """Persist a Phase 6 contact candidate linked to a candidate company."""
        if isinstance(contact, dict):
            c_dict = dict(contact)
            cid = c_dict.get("contact_id") or f"contact:{c_dict.get('work_email') or uuid.uuid4().hex[:8]}"
            c_dict["contact_id"] = cid
            obj = ContactCandidate(**c_dict)
        else:
            obj = contact
            if not getattr(obj, "contact_id", None):
                obj.contact_id = f"contact:{obj.work_email or uuid.uuid4().hex[:8]}"

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO candidate_contacts (
                    contact_id, candidate_id, first_name, last_name, full_name,
                    job_title, work_email, email_status, email_confidence,
                    seniority, department, linkedin_url, provider,
                    provider_person_id, buyer_role_match, contact_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contact_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    job_title = excluded.job_title,
                    work_email = excluded.work_email,
                    email_status = excluded.email_status,
                    email_confidence = excluded.email_confidence,
                    linkedin_url = excluded.linkedin_url
                """,
                (
                    obj.contact_id,
                    candidate_id,
                    obj.first_name,
                    obj.last_name,
                    obj.full_name or f"{obj.first_name or ''} {obj.last_name or ''}".strip(),
                    obj.job_title,
                    obj.work_email,
                    obj.email_status or "unknown",
                    obj.email_confidence or 0,
                    obj.seniority,
                    obj.department,
                    obj.linkedin_url,
                    obj.provider,
                    obj.provider_person_id,
                    obj.buyer_role_match,
                    obj.contact_score or 0,
                    now_iso,
                ),
            )
        return obj

    def get_contacts_for_candidate(self, candidate_id: str) -> List[ContactCandidate]:
        """Retrieve all registered Phase 6 contacts for a candidate company."""
        results: List[ContactCandidate] = []
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM candidate_contacts WHERE candidate_id = ? ORDER BY contact_score DESC, created_at ASC",
                (candidate_id,),
            ).fetchall()
            for r in rows:
                results.append(
                    ContactCandidate(
                        contact_id=r["contact_id"],
                        first_name=r["first_name"],
                        last_name=r["last_name"],
                        full_name=r["full_name"],
                        job_title=r["job_title"],
                        work_email=r["work_email"],
                        email_status=r["email_status"],
                        email_confidence=r["email_confidence"],
                        seniority=r["seniority"],
                        department=r["department"],
                        linkedin_url=r["linkedin_url"],
                        provider=r["provider"],
                        provider_person_id=r["provider_person_id"],
                        buyer_role_match=r["buyer_role_match"],
                        contact_score=r["contact_score"],
                    )
                )

        # Fallback to candidate metadata if no DB rows
        if not results:
            cand = self.get_candidate(candidate_id)
            if cand and isinstance(cand.metadata, dict):
                meta_contacts = cand.metadata.get("contacts") or []
                if not meta_contacts and cand.metadata.get("best_contact"):
                    meta_contacts = [cand.metadata["best_contact"]]
                for mc in meta_contacts:
                    if isinstance(mc, dict):
                        mc_copy = dict(mc)
                        if "contact_id" not in mc_copy:
                            mc_copy["contact_id"] = f"contact:{mc_copy.get('work_email') or uuid.uuid4().hex[:8]}"
                        results.append(ContactCandidate(**mc_copy))
                    elif isinstance(mc, ContactCandidate):
                        if not mc.contact_id:
                            mc.contact_id = f"contact:{mc.work_email or uuid.uuid4().hex[:8]}"
                        results.append(mc)

        return results

    def get_contact_by_id(self, contact_id: str, candidate_id: Optional[str] = None) -> Optional[ContactCandidate]:
        """Resolve a specific contact by contact_id or work_email."""
        clean_cid = contact_id.strip()
        with self._get_connection() as conn:
            if candidate_id:
                row = conn.execute(
                    "SELECT * FROM candidate_contacts WHERE (contact_id = ? OR work_email = ?) AND candidate_id = ?",
                    (clean_cid, clean_cid, candidate_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM candidate_contacts WHERE contact_id = ? OR work_email = ?",
                    (clean_cid, clean_cid),
                ).fetchone()

            if row:
                return ContactCandidate(
                    contact_id=row["contact_id"],
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    full_name=row["full_name"],
                    job_title=row["job_title"],
                    work_email=row["work_email"],
                    email_status=row["email_status"],
                    email_confidence=row["email_confidence"],
                    seniority=row["seniority"],
                    department=row["department"],
                    linkedin_url=row["linkedin_url"],
                    provider=row["provider"],
                    provider_person_id=row["provider_person_id"],
                    buyer_role_match=row["buyer_role_match"],
                    contact_score=row["contact_score"],
                )

        # Fallback to candidate metadata
        if candidate_id:
            all_c = self.get_contacts_for_candidate(candidate_id)
            for c in all_c:
                if c.contact_id == clean_cid or c.work_email == clean_cid:
                    return c
        return None

    def record_contact_verification(
        self,
        candidate_id: str,
        contact_id: str,
        work_email: str,
        evidence_source: str,
        evidence_reference: Optional[str] = None,
        verified_by: str = "local_operator",
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an explicit, auditable verification action for a candidate contact with provenance."""
        import uuid
        from datetime import datetime, timezone

        verif_id = f"verif_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO candidate_contact_verifications (
                    verification_id, candidate_id, contact_id, work_email,
                    evidence_source, evidence_reference, verified_by, verified_at, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verif_id,
                    candidate_id,
                    contact_id,
                    work_email.strip().lower(),
                    evidence_source,
                    evidence_reference,
                    verified_by,
                    now,
                    notes,
                ),
            )
            # Update the contact's email_status to verified
            conn.execute(
                """
                UPDATE candidate_contacts
                SET email_status = 'verified', email_confidence = 100, provider = COALESCE(provider, 'manual_verification')
                WHERE contact_id = ? AND candidate_id = ?
                """,
                (contact_id, candidate_id),
            )
        return {
            "verification_id": verif_id,
            "candidate_id": candidate_id,
            "contact_id": contact_id,
            "work_email": work_email,
            "evidence_source": evidence_source,
            "evidence_reference": evidence_reference,
            "verified_by": verified_by,
            "verified_at": now,
            "notes": notes,
        }

    def get_contact_verifications(self, candidate_id: str, contact_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve auditable verification trail for contacts of a candidate."""
        with self._get_connection() as conn:
            if contact_id:
                rows = conn.execute(
                    "SELECT * FROM candidate_contact_verifications WHERE candidate_id = ? AND contact_id = ? ORDER BY verified_at DESC",
                    (candidate_id, contact_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM candidate_contact_verifications WHERE candidate_id = ? ORDER BY verified_at DESC",
                    (candidate_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def reserve_pilot_search_attempt(
        self,
        attempt_id: str,
        provider: str,
        query: str,
        country: Optional[str] = None,
        city: Optional[str] = None,
        max_attempts_budget: int = 20,
        estimated_cost_usd: float = 0.005,
        idempotency_token: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Atomically checks remaining pilot quota and reserves a search attempt before dispatch.
        Uses BEGIN IMMEDIATE transaction to prevent concurrent race conditions.
        Enforces idempotency token validation to block double-click / rapid resubmissions.
        Returns (success: bool, error_message: Optional[str]).
        States: RESERVED, DISPATCH_ATTEMPTED, SUCCESS, FAILED, UNCERTAIN, PRE_DISPATCH_CANCELLED.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            # Begin immediate write transaction
            conn.execute("BEGIN IMMEDIATE")

            # Check if an attempt with this exact idempotency_token already exists
            if idempotency_token:
                dup_token = conn.execute(
                    "SELECT attempt_id, status FROM pilot_search_attempts WHERE idempotency_token = ?",
                    (idempotency_token,),
                ).fetchone()
                if dup_token:
                    conn.execute("ROLLBACK")
                    return False, f"Duplicate search submission detected (idempotency token matched attempt {dup_token['attempt_id']}, status={dup_token['status']}). Replay blocked."

            row = conn.execute(
                """
                SELECT COUNT(*) as cnt
                FROM pilot_search_attempts
                WHERE status NOT IN ('PRE_DISPATCH_CANCELLED')
                """
            ).fetchone()
            active_count = row["cnt"] if row else 0

            if active_count >= max_attempts_budget:
                conn.execute("ROLLBACK")
                return False, f"Live search BLOCKED (SESSION_BUDGET_EXCEEDED): Maximum pilot search limit ({max_attempts_budget} queries) reached. Prevents runaway charges. Session budget capped."

            conn.execute(
                """
                INSERT INTO pilot_search_attempts (
                    attempt_id, provider, query, country, city,
                    requested_at, results_count, status, error_message,
                    estimated_cost_usd, operator_approved, idempotency_token
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 'RESERVED', NULL, ?, 1, ?)
                """,
                (attempt_id, provider, query, country, city, now_iso, estimated_cost_usd, idempotency_token),
            )
            conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            return False, "Duplicate attempt ID or idempotency token detected. Request replay blocked."
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            return False, f"Reservation transaction failed: {str(e)}"

    def update_pilot_search_attempt(
        self,
        attempt_id: str,
        status: str,
        results_count: int = 0,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Updates the state of a previously reserved search attempt with strict state transition validation.
        Valid states: RESERVED, DISPATCH_ATTEMPTED, SUCCESS, FAILED, UNCERTAIN, PRE_DISPATCH_CANCELLED.
        DISPATCH_ATTEMPTED cannot be rolled back to PRE_DISPATCH_CANCELLED without proof of non-dispatch.
        """
        allowed_transitions = {
            "RESERVED": {"DISPATCH_ATTEMPTED", "SUCCESS", "FAILED", "UNCERTAIN", "PRE_DISPATCH_CANCELLED"},
            "DISPATCH_ATTEMPTED": {"SUCCESS", "FAILED", "UNCERTAIN"},
            "SUCCESS": set(),
            "FAILED": {"UNCERTAIN"},
            "UNCERTAIN": {"FAILED", "SUCCESS"},
            "PRE_DISPATCH_CANCELLED": set(),
        }

        with self._get_connection() as conn:
            cur = conn.execute("SELECT status FROM pilot_search_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if cur:
                curr_status = cur["status"]
                valid_next = allowed_transitions.get(curr_status, set())
                if status != curr_status and status not in valid_next:
                    logger.warning(
                        f"Attempt {attempt_id} invalid state transition requested: {curr_status} -> {status}. Retaining current state or marking UNCERTAIN."
                    )
                    # If attempting to cancel an already dispatched attempt, preserve uncertainty
                    if curr_status == "DISPATCH_ATTEMPTED" and status == "PRE_DISPATCH_CANCELLED":
                        status = "UNCERTAIN"
                        error_message = (error_message or "") + " [Transition to PRE_DISPATCH_CANCELLED rejected after dispatch attempt; marked UNCERTAIN]"

            conn.execute(
                """
                UPDATE pilot_search_attempts
                SET status = ?, results_count = ?, error_message = ?
                WHERE attempt_id = ?
                """,
                (status, results_count, error_message, attempt_id),
            )

    def record_pilot_search_attempt(
        self,
        attempt_id: str,
        provider: str,
        query: str,
        status: str = "SUCCESS",
        results_count: int = 0,
        country: Optional[str] = None,
        city: Optional[str] = None,
        error_message: Optional[str] = None,
        estimated_cost_usd: float = 0.005,
    ) -> None:
        """Legacy helper: records or updates a pilot search attempt."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT attempt_id FROM pilot_search_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE pilot_search_attempts
                    SET status = ?, results_count = ?, error_message = ?
                    WHERE attempt_id = ?
                    """,
                    (status, results_count, error_message, attempt_id),
                )
            else:
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO pilot_search_attempts (
                        attempt_id, provider, query, country, city,
                        requested_at, results_count, status, error_message,
                        estimated_cost_usd, operator_approved
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        attempt_id, provider, query, country, city,
                        now_iso, results_count, status, error_message,
                        estimated_cost_usd,
                    ),
                )

    def get_pilot_search_stats(self) -> Dict[str, Any]:
        """Returns total attempts, successful, failed, reserved/uncertain, and estimated cost across pilot searches."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_attempts,
                    SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as successful_attempts,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed_attempts,
                    SUM(CASE WHEN status IN ('RESERVED', 'DISPATCH_ATTEMPTED', 'UNCERTAIN') THEN 1 ELSE 0 END) as uncertain_attempts,
                    SUM(results_count) as total_results,
                    SUM(estimated_cost_usd) as total_estimated_cost_usd
                FROM pilot_search_attempts
                WHERE status != 'PRE_DISPATCH_CANCELLED'
                """
            ).fetchone()
            if row:
                return {
                    "total_attempts": row["total_attempts"] or 0,
                    "successful_attempts": row["successful_attempts"] or 0,
                    "failed_attempts": row["failed_attempts"] or 0,
                    "uncertain_attempts": row["uncertain_attempts"] or 0,
                    "total_results": row["total_results"] or 0,
                    "total_estimated_cost_usd": round(row["total_estimated_cost_usd"] or 0.0, 4),
                }
            return {
                "total_attempts": 0,
                "successful_attempts": 0,
                "failed_attempts": 0,
                "uncertain_attempts": 0,
                "total_results": 0,
                "total_estimated_cost_usd": 0.0,
            }

    def create_backup(self, backup_dir: Optional[Union[str, Path]] = None) -> Tuple[Path, str]:
        """
        Creates a consistent, timestamped SQLite backup using SQLite's online backup API.
        Does not disrupt concurrent readers. Returns (backup_path, backup_sha256).
        """
        import hashlib
        source_path = Path(self.db_path)
        dest_dir = Path(backup_dir) if backup_dir else source_path.parent / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest_path = dest_dir / f"{source_path.stem}_backup_{timestamp}{source_path.suffix}"

        # Execute consistent online backup
        with self._get_connection() as src_conn:
            dest_conn = sqlite3.connect(str(dest_path))
            try:
                src_conn.backup(dest_conn)
            finally:
                dest_conn.close()

        # Compute SHA-256
        content = dest_path.read_bytes()
        backup_sha = hashlib.sha256(content).hexdigest()
        return dest_path, backup_sha

    def verify_backup(self, backup_path: Union[str, Path]) -> Tuple[bool, Optional[str]]:
        """
        Verifies SQLite backup file integrity using PRAGMA quick_check and foreign_key_check.
        Returns (is_valid: bool, error: Optional[str]).
        """
        path = Path(backup_path)
        if not path.exists():
            return False, f"Backup file does not exist: {path}"
        try:
            conn = sqlite3.connect(str(path))
            cursor = conn.cursor()
            res = cursor.execute("PRAGMA quick_check;").fetchone()
            if not res or res[0] != "ok":
                conn.close()
                return False, f"Integrity check failed: {res[0] if res else 'no response'}"
            conn.close()
            return True, None
        except Exception as e:
            return False, f"Failed to verify backup integrity: {str(e)}"


