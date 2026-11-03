"""
sales_engine/research/research_store.py
SQLite-based persistent snapshot storage for company research evidence and normalized datasets.
Strictly decoupled from the operational CRM/LeadRegister database.
"""

from __future__ import annotations

import gzip
import os
import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CURRENT_DATASET_SCHEMA_VERSION = "1.0.0"
CURRENT_CRAWLER_VERSION = "2.0.0"
CURRENT_PARSER_VERSION = "2.0.0"

DEFAULT_SNAPSHOT_DIR = Path("data/research_snapshots")
DEFAULT_RESEARCH_DB_PATH = DEFAULT_SNAPSHOT_DIR / "research_cache.sqlite3"


@dataclass
class PageEvidence:
    """Normalized evidence representation for an individual crawled page."""
    snapshot_id: str
    evidence_id: str
    requested_url: str
    resolved_url: str
    page_type: str
    http_status: int
    retrieval_status: str  # "FETCH_SUCCESS", "FETCH_FAILED", "BLOCKED_UNSAFE"
    retrieved_at: str
    title: Optional[str]
    meta_description: Optional[str]
    content_sha256: str
    cleaned_text: str
    raw_html: Optional[str] = None  # Full uncompressed HTML for in-memory use
    links: List[Dict[str, str]] = field(default_factory=list)
    forms: List[Dict[str, Any]] = field(default_factory=list)
    structured_data: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None


@dataclass
class AnalysisChunkRecord:
    """Durable checkpoint record for a single analysis chunk execution."""
    chunk_record_id: str
    analysis_id: str
    candidate_id: str
    snapshot_id: str
    snapshot_checksum: str
    chunk_index: int
    total_chunks: int
    chunk_hash: str
    provider: str
    model: str
    schema_version: str
    prompt_version: str
    status: str  # "COMPLETED", "FAILED", "UNCERTAIN_TIMEOUT"
    created_at: str
    completed_at: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_estimate: Optional[float] = None
    raw_response: Optional[str] = None
    proposed_fields: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None


@dataclass
class CompanyAnalysisRecord:
    """Durable record of an LLM analysis execution linked to a research snapshot."""
    analysis_id: str
    candidate_id: str
    snapshot_id: str
    provider: str
    model: str
    created_at: str
    status: str  # "SUCCESS", "SCHEMA_INVALID", "FAILED", "NOT_CONFIGURED"
    proposed_fields: Dict[str, Any] = field(default_factory=dict)
    raw_response_text: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_estimate: Optional[float] = None
    diagnostic_info: Optional[str] = None
    analyzed_pages_count: int = 0
    chunks_count: int = 0
    omitted_pages_count: int = 0
    provider_calls_count: int = 0
    omitted_pages_reasons: Dict[str, str] = field(default_factory=dict)


@dataclass
class ResearchSnapshot:
    """Metadata and status record for an immutable research crawl snapshot."""
    snapshot_id: str
    candidate_id: str
    canonical_domain: str
    company_name: str
    created_at: str
    completed_at: Optional[str]
    crawler_version: str
    parser_version: str
    dataset_schema_version: str
    research_status: str  # "complete", "partial", "failed"
    pages_attempted: int
    pages_succeeded: int
    content_checksum: str  # Combined SHA-256 of all page contents
    diagnostic_info: Optional[str] = None
    pages: List[PageEvidence] = field(default_factory=list)


class ResearchSnapshotStore:
    """
    Authoritative SQLite store for research snapshots and page evidence.
    Supports atomic transactions, zlib/gzip HTML compression, and immutable versioning.
    """

    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is not None:
            self.db_path = Path(db_path)
        else:
            env_db = os.getenv("SALESAI_RESEARCH_CACHE_DB")
            if env_db:
                self.db_path = Path(env_db)
            else:
                self.db_path = DEFAULT_RESEARCH_DB_PATH

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS research_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    canonical_domain TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    crawler_version TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    dataset_schema_version TEXT NOT NULL,
                    research_status TEXT NOT NULL,
                    pages_attempted INTEGER NOT NULL DEFAULT 0,
                    pages_succeeded INTEGER NOT NULL DEFAULT 0,
                    content_checksum TEXT NOT NULL,
                    diagnostic_info TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_snapshots_candidate_id
                    ON research_snapshots (candidate_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_snapshots_canonical_domain
                    ON research_snapshots (canonical_domain);

                CREATE TABLE IF NOT EXISTS page_evidences (
                    page_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    page_order INTEGER NOT NULL,
                    requested_url TEXT NOT NULL,
                    resolved_url TEXT NOT NULL,
                    page_type TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    retrieval_status TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    title TEXT,
                    meta_description TEXT,
                    content_sha256 TEXT NOT NULL,
                    cleaned_text TEXT NOT NULL,
                    compressed_raw_html BLOB,
                    links_json TEXT,
                    forms_json TEXT,
                    structured_data_json TEXT,
                    error_message TEXT,
                    FOREIGN KEY (snapshot_id) REFERENCES research_snapshots(snapshot_id) ON DELETE CASCADE,
                    UNIQUE(snapshot_id, evidence_id)
                );

                CREATE INDEX IF NOT EXISTS idx_page_evidences_snapshot
                    ON page_evidences (snapshot_id, page_order ASC);

                CREATE TABLE IF NOT EXISTS analysis_chunk_checkpoints (
                    chunk_record_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    snapshot_checksum TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    chunk_hash TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cost_estimate REAL,
                    raw_response TEXT,
                    proposed_fields_json TEXT NOT NULL,
                    error_message TEXT,
                    FOREIGN KEY (snapshot_id) REFERENCES research_snapshots(snapshot_id) ON DELETE CASCADE,
                    UNIQUE(candidate_id, snapshot_id, chunk_hash, model)
                );

                CREATE INDEX IF NOT EXISTS idx_chunk_analysis
                    ON analysis_chunk_checkpoints (analysis_id, chunk_index ASC);

                CREATE INDEX IF NOT EXISTS idx_chunk_candidate_snapshot
                    ON analysis_chunk_checkpoints (candidate_id, snapshot_id);

                CREATE TABLE IF NOT EXISTS company_analysis_records (
                    analysis_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    proposed_fields_json TEXT NOT NULL,
                    raw_response_text TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cost_estimate REAL,
                    diagnostic_info TEXT,
                    analyzed_pages_count INTEGER DEFAULT 0,
                    chunks_count INTEGER DEFAULT 0,
                    omitted_pages_count INTEGER DEFAULT 0,
                    provider_calls_count INTEGER DEFAULT 0,
                    omitted_pages_reasons_json TEXT,
                    FOREIGN KEY (snapshot_id) REFERENCES research_snapshots(snapshot_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_analysis_candidate
                    ON company_analysis_records (candidate_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_analysis_snapshot
                    ON company_analysis_records (snapshot_id, created_at DESC);
            """)
            # Migrate company_analysis_records for new columns if they do not exist
            cols_analysis = {r[1] for r in conn.execute("PRAGMA table_info(company_analysis_records);").fetchall()}
            if "analyzed_pages_count" not in cols_analysis:
                conn.execute("ALTER TABLE company_analysis_records ADD COLUMN analyzed_pages_count INTEGER DEFAULT 0;")
            if "chunks_count" not in cols_analysis:
                conn.execute("ALTER TABLE company_analysis_records ADD COLUMN chunks_count INTEGER DEFAULT 0;")
            if "omitted_pages_count" not in cols_analysis:
                conn.execute("ALTER TABLE company_analysis_records ADD COLUMN omitted_pages_count INTEGER DEFAULT 0;")
            if "provider_calls_count" not in cols_analysis:
                conn.execute("ALTER TABLE company_analysis_records ADD COLUMN provider_calls_count INTEGER DEFAULT 0;")
            if "omitted_pages_reasons_json" not in cols_analysis:
                conn.execute("ALTER TABLE company_analysis_records ADD COLUMN omitted_pages_reasons_json TEXT;")
            conn.commit()

    def create_snapshot(
        self,
        candidate_id: str,
        canonical_domain: str,
        company_name: str,
        pages: List[PageEvidence],
        research_status: str = "complete",
        diagnostic_info: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> ResearchSnapshot:
        """
        Atomically persist a completed or partial research crawl snapshot.
        Compresses raw HTML payloads with gzip to preserve disk budget.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        sid = snapshot_id or f"snap_{uuid.uuid4().hex[:16]}"
        c_at = created_at or now_iso

        pages_attempted = len(pages)
        pages_succeeded = sum(1 for p in pages if p.retrieval_status == "FETCH_SUCCESS")

        # Determine overall status if not explicitly set
        if pages_succeeded == 0 and pages_attempted > 0:
            actual_status = "failed"
        elif pages_succeeded < pages_attempted:
            actual_status = "partial" if research_status == "complete" else research_status
        else:
            actual_status = research_status

        # Deterministic content checksum across all successful page contents
        hasher = hashlib.sha256()
        for idx, p in enumerate(pages, 1):
            p.snapshot_id = sid
            if not p.evidence_id:
                p.evidence_id = f"EV_{idx:02d}"
            hasher.update(p.evidence_id.encode("utf-8"))
            hasher.update(p.content_sha256.encode("utf-8"))
        combined_checksum = hasher.hexdigest()

        snapshot = ResearchSnapshot(
            snapshot_id=sid,
            candidate_id=candidate_id,
            canonical_domain=canonical_domain,
            company_name=company_name,
            created_at=c_at,
            completed_at=now_iso,
            crawler_version=CURRENT_CRAWLER_VERSION,
            parser_version=CURRENT_PARSER_VERSION,
            dataset_schema_version=CURRENT_DATASET_SCHEMA_VERSION,
            research_status=actual_status,
            pages_attempted=pages_attempted,
            pages_succeeded=pages_succeeded,
            content_checksum=combined_checksum,
            diagnostic_info=diagnostic_info,
            pages=pages,
        )

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO research_snapshots (
                    snapshot_id, candidate_id, canonical_domain, company_name,
                    created_at, completed_at, crawler_version, parser_version,
                    dataset_schema_version, research_status, pages_attempted,
                    pages_succeeded, content_checksum, diagnostic_info
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.candidate_id,
                    snapshot.canonical_domain,
                    snapshot.company_name,
                    snapshot.created_at,
                    snapshot.completed_at,
                    snapshot.crawler_version,
                    snapshot.parser_version,
                    snapshot.dataset_schema_version,
                    snapshot.research_status,
                    snapshot.pages_attempted,
                    snapshot.pages_succeeded,
                    snapshot.content_checksum,
                    snapshot.diagnostic_info,
                ),
            )

            for order_idx, p in enumerate(pages, 1):
                page_id = f"{sid}_{p.evidence_id}"
                comp_html = None
                if p.raw_html:
                    comp_html = gzip.compress(p.raw_html.encode("utf-8"))

                conn.execute(
                    """
                    INSERT INTO page_evidences (
                        page_id, snapshot_id, evidence_id, page_order,
                        requested_url, resolved_url, page_type, http_status,
                        retrieval_status, retrieved_at, title, meta_description,
                        content_sha256, cleaned_text, compressed_raw_html,
                        links_json, forms_json, structured_data_json, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        page_id,
                        p.snapshot_id,
                        p.evidence_id,
                        order_idx,
                        p.requested_url,
                        p.resolved_url,
                        p.page_type,
                        p.http_status,
                        p.retrieval_status,
                        p.retrieved_at,
                        p.title,
                        p.meta_description,
                        p.content_sha256,
                        p.cleaned_text,
                        comp_html,
                        json.dumps(p.links, ensure_ascii=False),
                        json.dumps(p.forms, ensure_ascii=False),
                        json.dumps(p.structured_data, ensure_ascii=False),
                        p.error_message,
                    ),
                )
            conn.commit()

        logger.info(f"Persisted research snapshot {sid} for candidate {candidate_id} ({pages_succeeded}/{pages_attempted} pages)")
        return snapshot

    def get_latest_snapshot(self, candidate_id: str, load_raw_html: bool = False) -> Optional[ResearchSnapshot]:
        """Retrieve the most recent snapshot for a candidate."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM research_snapshots
                WHERE candidate_id = ?
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                (candidate_id,),
            ).fetchone()

            if not row:
                return None

            return self._build_snapshot_from_row(conn, row, load_raw_html=load_raw_html)

    def get_snapshot_by_id(self, snapshot_id: str, load_raw_html: bool = False) -> Optional[ResearchSnapshot]:
        """Retrieve a specific snapshot by its unique ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_snapshots WHERE snapshot_id = ?;",
                (snapshot_id,),
            ).fetchone()

            if not row:
                return None

            return self._build_snapshot_from_row(conn, row, load_raw_html=load_raw_html)

    def list_snapshots_for_candidate(self, candidate_id: str) -> List[Dict[str, Any]]:
        """List summary metadata of all historical snapshots for a candidate."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_id, candidate_id, canonical_domain, company_name,
                       created_at, completed_at, research_status, pages_attempted,
                       pages_succeeded, content_checksum
                FROM research_snapshots
                WHERE candidate_id = ?
                ORDER BY created_at DESC;
                """,
                (candidate_id,),
            ).fetchall()

            return [dict(r) for r in rows]

    def get_raw_html(self, snapshot_id: str, target_identifier: str) -> Optional[str]:
        """
        Decompress and retrieve raw HTML for a single stored evidence item.
        target_identifier can be an evidence_id (e.g. 'EV_01') or a URL.
        Fails safely and returns None if corrupt or missing.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT compressed_raw_html FROM page_evidences
                WHERE snapshot_id = ? AND (evidence_id = ? OR requested_url = ? OR resolved_url = ?);
                """,
                (snapshot_id, target_identifier, target_identifier, target_identifier),
            ).fetchone()

            if not row or not row["compressed_raw_html"]:
                return None

            try:
                return gzip.decompress(row["compressed_raw_html"]).decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"Failed to decompress raw HTML for snapshot {snapshot_id}, target {target_identifier}: {e}")
                return None

    def export_normalized_dataset(self, snapshot_id: str) -> Dict[str, Any]:
        """
        Export a provider-independent, deterministic, frozen JSON dataset
        suitable for reproducible multi-model LLM benchmarking.
        """
        snapshot = self.get_snapshot_by_id(snapshot_id, load_raw_html=False)
        if not snapshot:
            raise KeyError(f"Snapshot ID not found: {snapshot_id}")

        evidence_docs = []
        combined_text_parts = []

        for p in snapshot.pages:
            doc = {
                "snapshot_id": snapshot.snapshot_id,
                "evidence_id": p.evidence_id,
                "source_url": p.resolved_url,
                "page_type": p.page_type,
                "title": p.title or "",
                "retrieved_at": p.retrieved_at,
                "content_sha256": p.content_sha256,
                "cleaned_text": p.cleaned_text,
                "links": p.links,
                "forms": p.forms,
                "structured_data": p.structured_data,
            }
            evidence_docs.append(doc)

            if p.retrieval_status == "FETCH_SUCCESS" and p.cleaned_text.strip():
                combined_text_parts.append(
                    f"--- DOCUMENT {p.evidence_id} ---\n"
                    f"URL: {p.resolved_url}\n"
                    f"Type: {p.page_type}\n"
                    f"Title: {p.title or ''}\n"
                    f"Content:\n{p.cleaned_text.strip()}\n"
                )

        combined_research_text = "\n".join(combined_text_parts)

        # Compute stable dataset SHA-256 over normalized canonical JSON representation
        raw_dataset_bytes = json.dumps(
            {
                "snapshot_id": snapshot.snapshot_id,
                "candidate_id": snapshot.candidate_id,
                "canonical_domain": snapshot.canonical_domain,
                "dataset_schema_version": snapshot.dataset_schema_version,
                "evidence_documents": evidence_docs,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        dataset_sha256 = hashlib.sha256(raw_dataset_bytes).hexdigest()

        return {
            "dataset_version": snapshot.dataset_schema_version,
            "dataset_sha256": dataset_sha256,
            "dataset_checksum_sha256": snapshot.content_checksum,
            "snapshot_id": snapshot.snapshot_id,
            "candidate_id": snapshot.candidate_id,
            "canonical_domain": snapshot.canonical_domain,
            "company_name": snapshot.company_name,
            "created_at": snapshot.created_at,
            "research_status": snapshot.research_status,
            "pages_count": len(evidence_docs),
            "total_evidence_documents": len(evidence_docs),
            "evidence_documents": evidence_docs,
            "combined_research_text": combined_research_text,
        }

    def save_chunk_checkpoint(self, record: AnalysisChunkRecord) -> None:
        """Persist or update an analysis chunk execution checkpoint."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO analysis_chunk_checkpoints (
                    chunk_record_id, analysis_id, candidate_id, snapshot_id,
                    snapshot_checksum, chunk_index, total_chunks, chunk_hash,
                    provider, model, schema_version, prompt_version, status,
                    created_at, completed_at, input_tokens, output_tokens,
                    cost_estimate, raw_response, proposed_fields_json, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.chunk_record_id,
                    record.analysis_id,
                    record.candidate_id,
                    record.snapshot_id,
                    record.snapshot_checksum,
                    record.chunk_index,
                    record.total_chunks,
                    record.chunk_hash,
                    record.provider,
                    record.model,
                    record.schema_version,
                    record.prompt_version,
                    record.status,
                    record.created_at,
                    record.completed_at,
                    record.input_tokens,
                    record.output_tokens,
                    record.cost_estimate,
                    record.raw_response,
                    json.dumps(record.proposed_fields, ensure_ascii=False),
                    record.error_message,
                ),
            )
            conn.commit()

    def get_chunk_checkpoints(
        self,
        candidate_id: str,
        snapshot_id: str,
        model: Optional[str] = None
    ) -> List[AnalysisChunkRecord]:
        """Retrieve chunk checkpoints for candidate and snapshot, ordered by chunk_index."""
        with self._get_connection() as conn:
            if model:
                rows = conn.execute(
                    """
                    SELECT * FROM analysis_chunk_checkpoints
                    WHERE candidate_id = ? AND snapshot_id = ? AND model = ?
                    ORDER BY chunk_index ASC;
                    """,
                    (candidate_id, snapshot_id, model),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM analysis_chunk_checkpoints
                    WHERE candidate_id = ? AND snapshot_id = ?
                    ORDER BY chunk_index ASC;
                    """,
                    (candidate_id, snapshot_id),
                ).fetchall()
            return [self._row_to_chunk_record(r) for r in rows]

    def get_chunk_checkpoint_by_hash(
        self,
        candidate_id: str,
        snapshot_id: str,
        chunk_hash: str,
        model: str,
    ) -> Optional[AnalysisChunkRecord]:
        """Retrieve a specific chunk checkpoint by candidate, snapshot, chunk hash, and model."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM analysis_chunk_checkpoints
                WHERE candidate_id = ? AND snapshot_id = ? AND chunk_hash = ? AND model = ?
                LIMIT 1;
                """,
                (candidate_id, snapshot_id, chunk_hash, model),
            ).fetchone()
            if row:
                return self._row_to_chunk_record(row)
            return None

    def _row_to_chunk_record(self, row: sqlite3.Row) -> AnalysisChunkRecord:
        pf = {}
        if row["proposed_fields_json"]:
            try:
                pf = json.loads(row["proposed_fields_json"])
            except Exception:
                pass
        return AnalysisChunkRecord(
            chunk_record_id=row["chunk_record_id"],
            analysis_id=row["analysis_id"],
            candidate_id=row["candidate_id"],
            snapshot_id=row["snapshot_id"],
            snapshot_checksum=row["snapshot_checksum"],
            chunk_index=row["chunk_index"],
            total_chunks=row["total_chunks"],
            chunk_hash=row["chunk_hash"],
            provider=row["provider"],
            model=row["model"],
            schema_version=row["schema_version"],
            prompt_version=row["prompt_version"],
            status=row["status"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_estimate=row["cost_estimate"],
            raw_response=row["raw_response"],
            proposed_fields=pf,
            error_message=row["error_message"],
        )

    def save_analysis_record(self, record: CompanyAnalysisRecord) -> None:
        """Persist an LLM analysis record linked to a snapshot."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO company_analysis_records (
                    analysis_id, candidate_id, snapshot_id, provider, model,
                    created_at, status, proposed_fields_json, raw_response_text,
                    input_tokens, output_tokens, cost_estimate, diagnostic_info,
                    analyzed_pages_count, chunks_count, omitted_pages_count,
                    provider_calls_count, omitted_pages_reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.analysis_id,
                    record.candidate_id,
                    record.snapshot_id,
                    record.provider,
                    record.model,
                    record.created_at,
                    record.status,
                    json.dumps(record.proposed_fields, ensure_ascii=False),
                    record.raw_response_text,
                    record.input_tokens,
                    record.output_tokens,
                    record.cost_estimate,
                    record.diagnostic_info,
                    record.analyzed_pages_count,
                    record.chunks_count,
                    record.omitted_pages_count,
                    record.provider_calls_count,
                    json.dumps(record.omitted_pages_reasons, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_analysis_by_id(self, analysis_id: str) -> Optional[CompanyAnalysisRecord]:
        """Retrieve an analysis record by its ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM company_analysis_records WHERE analysis_id = ?;",
                (analysis_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_analysis(row)

    def get_latest_analysis(
        self,
        candidate_id: str,
        snapshot_id: Optional[str] = None
    ) -> Optional[CompanyAnalysisRecord]:
        """Retrieve the most recent analysis record for a candidate (optionally filtered by snapshot)."""
        with self._get_connection() as conn:
            if snapshot_id:
                row = conn.execute(
                    """
                    SELECT * FROM company_analysis_records
                    WHERE candidate_id = ? AND snapshot_id = ?
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (candidate_id, snapshot_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM company_analysis_records
                    WHERE candidate_id = ?
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (candidate_id,),
                ).fetchone()

            if not row:
                return None
            return self._row_to_analysis(row)

    def get_latest_successful_analysis(
        self,
        candidate_id: str,
        snapshot_id: Optional[str] = None
    ) -> Optional[CompanyAnalysisRecord]:
        """Retrieve the most recent successful analysis record for a candidate."""
        with self._get_connection() as conn:
            if snapshot_id:
                row = conn.execute(
                    """
                    SELECT * FROM company_analysis_records
                    WHERE candidate_id = ? AND snapshot_id = ? AND status = 'SUCCESS'
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (candidate_id, snapshot_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM company_analysis_records
                    WHERE candidate_id = ? AND status = 'SUCCESS'
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (candidate_id,),
                ).fetchone()

            if not row:
                return None
            return self._row_to_analysis(row)

    def list_analyses_for_candidate(self, candidate_id: str) -> List[CompanyAnalysisRecord]:
        """List all historical analysis records for a candidate."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM company_analysis_records
                WHERE candidate_id = ?
                ORDER BY created_at DESC;
                """,
                (candidate_id,),
            ).fetchall()
            return [self._row_to_analysis(r) for r in rows]

    def _row_to_analysis(self, row: sqlite3.Row) -> CompanyAnalysisRecord:
        proposed = {}
        if row["proposed_fields_json"]:
            try:
                proposed = json.loads(row["proposed_fields_json"])
            except Exception:
                pass

        row_keys = row.keys()
        omitted_reasons = {}
        if "omitted_pages_reasons_json" in row_keys and row["omitted_pages_reasons_json"]:
            try:
                omitted_reasons = json.loads(row["omitted_pages_reasons_json"])
            except Exception:
                pass

        return CompanyAnalysisRecord(
            analysis_id=row["analysis_id"],
            candidate_id=row["candidate_id"],
            snapshot_id=row["snapshot_id"],
            provider=row["provider"],
            model=row["model"],
            created_at=row["created_at"],
            status=row["status"],
            proposed_fields=proposed,
            raw_response_text=row["raw_response_text"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_estimate=row["cost_estimate"],
            diagnostic_info=row["diagnostic_info"],
            analyzed_pages_count=row["analyzed_pages_count"] if "analyzed_pages_count" in row_keys and row["analyzed_pages_count"] is not None else 0,
            chunks_count=row["chunks_count"] if "chunks_count" in row_keys and row["chunks_count"] is not None else 0,
            omitted_pages_count=row["omitted_pages_count"] if "omitted_pages_count" in row_keys and row["omitted_pages_count"] is not None else 0,
            provider_calls_count=row["provider_calls_count"] if "provider_calls_count" in row_keys and row["provider_calls_count"] is not None else 0,
            omitted_pages_reasons=omitted_reasons,
        )

    def get_storage_stats(self) -> Dict[str, Any]:
        """Report SQLite file size and record counts for disk accounting."""
        size_bytes = 0
        if self.db_path.exists():
            size_bytes = self.db_path.stat().st_size

        with self._get_connection() as conn:
            snap_count = conn.execute("SELECT COUNT(*) FROM research_snapshots;").fetchone()[0]
            page_count = conn.execute("SELECT COUNT(*) FROM page_evidences;").fetchone()[0]

        return {
            "db_path": str(self.db_path),
            "db_size_bytes": size_bytes,
            "db_size_kb": round(size_bytes / 1024, 2),
            "snapshot_count": snap_count,
            "page_evidence_count": page_count,
        }

    def _build_snapshot_from_row(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        load_raw_html: bool = False
    ) -> ResearchSnapshot:
        sid = row["snapshot_id"]
        page_rows = conn.execute(
            """
            SELECT * FROM page_evidences
            WHERE snapshot_id = ?
            ORDER BY page_order ASC;
            """,
            (sid,),
        ).fetchall()

        pages = []
        for pr in page_rows:
            raw_html_val = None
            if load_raw_html and pr["compressed_raw_html"]:
                try:
                    raw_html_val = gzip.decompress(pr["compressed_raw_html"]).decode("utf-8", errors="replace")
                except Exception as e:
                    logger.warning(f"Failed to decompress raw HTML for {pr['evidence_id']}: {e}")

            links_list = []
            if pr["links_json"]:
                try:
                    links_list = json.loads(pr["links_json"])
                except Exception:
                    pass

            forms_list = []
            if pr["forms_json"]:
                try:
                    forms_list = json.loads(pr["forms_json"])
                except Exception:
                    pass

            structured_list = []
            if pr["structured_data_json"]:
                try:
                    structured_list = json.loads(pr["structured_data_json"])
                except Exception:
                    pass

            pages.append(
                PageEvidence(
                    snapshot_id=sid,
                    evidence_id=pr["evidence_id"],
                    requested_url=pr["requested_url"],
                    resolved_url=pr["resolved_url"],
                    page_type=pr["page_type"],
                    http_status=pr["http_status"],
                    retrieval_status=pr["retrieval_status"],
                    retrieved_at=pr["retrieved_at"],
                    title=pr["title"],
                    meta_description=pr["meta_description"],
                    content_sha256=pr["content_sha256"],
                    cleaned_text=pr["cleaned_text"],
                    raw_html=raw_html_val,
                    links=links_list,
                    forms=forms_list,
                    structured_data=structured_list,
                    error_message=pr["error_message"],
                )
            )

        return ResearchSnapshot(
            snapshot_id=row["snapshot_id"],
            candidate_id=row["candidate_id"],
            canonical_domain=row["canonical_domain"],
            company_name=row["company_name"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            crawler_version=row["crawler_version"],
            parser_version=row["parser_version"],
            dataset_schema_version=row["dataset_schema_version"],
            research_status=row["research_status"],
            pages_attempted=row["pages_attempted"],
            pages_succeeded=row["pages_succeeded"],
            content_checksum=row["content_checksum"],
            diagnostic_info=row["diagnostic_info"],
            pages=pages,
        )
