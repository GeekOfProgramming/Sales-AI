"""
Phase 10 Discovery & Market Intelligence CLI.

Provides command-line operator tools for multi-source candidate discovery,
historical data ingestion, candidate qualification, and watchlist inspection.

Absolute Invariant: ZERO SENDS. Discovery operations never send or draft emails.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier
from sales_engine.discovery_v2.bridges import Phase10ConsoleBridge
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.source_registry import SourceRegistry
from sales_engine.historical.importer import HistoricalImporter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def get_cli_services(db_path: Optional[str] = None):
    db = db_path or os.getenv("SALESAI_DB_PATH", os.getenv("SALESAI_CANDIDATES_DB", "data/sales_outreach.db"))
    store = CandidateStore(db_path=db)
    registry = SourceRegistry()
    from sales_engine.discovery_v2.bridges import Phase9AuthorityBridge
    authority_bridge = Phase9AuthorityBridge(db_path=store.db_path)
    classifier = CompanyClassifier(suppression_store=authority_bridge)
    engine = DiscoveryEngineV2(
        candidate_store=store,
        classifier=classifier,
        source_registry=registry,
    )
    bridge = Phase10ConsoleBridge(store=store, registry=registry)
    return store, registry, classifier, engine, bridge


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery_cli",
        description="SalesAI Phase 10 Candidate Discovery & Intelligence CLI (Zero-Send Invariant)",
    )
    parser.add_argument("--db", default=None, help="Path to SQLite candidates database")
    subparsers = parser.add_subparsers(dest="command", help="Discovery commands")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Run multi-source candidate discovery")
    run_parser.add_argument("--sources", nargs="*", default=None, help="List of source IDs or names to run")
    run_parser.add_argument("--query", default="BIM", help="Search query string")
    run_parser.add_argument("--mode", default="company_first", choices=["company_first", "job_first", "tender_first", "manual_seed"], help="Discovery mode")

    # Command: sources
    src_parser = subparsers.add_parser("sources", help="List configured sources and health")
    src_parser.add_argument("--enabled-only", action="store_true", help="Show only enabled sources")

    # Command: candidates
    cand_parser = subparsers.add_parser("candidates", help="List candidate companies")
    cand_parser.add_argument("--status", help="Filter by discovery status")
    cand_parser.add_argument("--tier", help="Filter by company tier (tier_1, tier_2, tier_3)")
    cand_parser.add_argument("--country", help="Filter by country")
    cand_parser.add_argument("--relationship-type", help="Filter by relationship type")
    cand_parser.add_argument("--limit", type=int, default=50, help="Maximum candidates to return")

    # Command: candidate
    det_parser = subparsers.add_parser("candidate", help="View candidate details")
    det_parser.add_argument("--id", required=True, help="Candidate ID")

    # Command: qualify
    qual_parser = subparsers.add_parser("qualify", help="Qualify a candidate company")
    qual_parser.add_argument("--id", required=True, help="Candidate ID to classify and qualify")

    # Command: watchlist
    watch_parser = subparsers.add_parser("watchlist", help="List candidates on the watchlist")

    # Command: watch
    add_watch_parser = subparsers.add_parser("watch", help="Add a candidate to the watchlist")
    add_watch_parser.add_argument("--id", required=True, help="Candidate ID")
    add_watch_parser.add_argument("--reason", required=True, help="Reason for monitoring")

    # Command: import-history
    hist_parser = subparsers.add_parser("import-history", help="Import historical spreadsheet (CSV/XLSX)")
    hist_parser.add_argument("--file", required=True, help="Path to CSV or XLSX file")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    store, registry, classifier, engine, bridge = get_cli_services(db_path=args.db)

    if args.command == "run":
        sources = args.sources or [s.id for s in registry.list_sources(enabled_only=True)]
        print(f"Starting discovery run across {len(sources)} sources (mode: {args.mode}, query: {args.query})...")
        rec = engine.run_multi_source_discovery(
            sources=sources,
            query=args.query,
            discovery_mode=args.mode,
        )
        print(f"Run completed: status={rec.status}, run_id={rec.run_id}")
        print(f"  Observations found: {rec.observations_found}")
        print(f"  New candidates: {rec.new_candidates}")
        print(f"  Updated candidates: {rec.updated_candidates}")
        print(f"  Errors: {len(rec.errors)}")
        return 0 if rec.status in ("completed", "partial_failure") else 1

    elif args.command == "sources":
        sources = registry.list_sources(enabled_only=args.enabled_only)
        health = registry.get_health_summary()
        print(f"{'ID':<25} {'Type':<18} {'Mode':<15} {'Status':<12} {'Success/Fail'}")
        print("-" * 80)
        for s in sources:
            h = health.get(s.id, health.get(s.name, {}))
            status = h.get("status", "unknown")
            counts = f"{h.get('success_count', 0)}/{h.get('failure_count', 0)}"
            print(f"{s.id:<25} {s.type:<18} {str(s.discovery_mode):<15} {status:<12} {counts}")
        return 0

    elif args.command == "candidates":
        cands = bridge.get_candidate_list_summary(
            status=args.status,
            relationship_type=args.relationship_type,
            tier=args.tier,
            limit=args.limit,
        )
        if args.country:
            cands = [c for c in cands if c.get("country", "").lower() == args.country.lower()]
        print(f"Found {len(cands)} candidates:")
        for c in cands[:args.limit]:
            print(f"- [{c.get('company_id')}] {c.get('name')} | domain={c.get('canonical_domain')} | tier={c.get('company_tier')} | status={c.get('status')} | cold_allowed={c.get('cold_outreach_allowed')}")
        return 0

    elif args.command == "candidate":
        detail = bridge.get_candidate_detail(args.id)
        if not detail:
            print(f"Error: Candidate {args.id} not found", file=sys.stderr)
            return 1
        comp = detail["company"]
        print(f"Company: {comp.get('name')} ({comp.get('company_id')})")
        print(f"Domain: {comp.get('canonical_domain')}")
        print(f"Country: {comp.get('country')} | City: {comp.get('city')}")
        print(f"Tier: {comp.get('company_tier')} | BIM Maturity: {comp.get('bim_maturity')}")
        print(f"Relationship: {comp.get('relationship_type')} | Opportunities: {comp.get('opportunity_types')}")
        print(f"Cold Outreach Allowed: {comp.get('cold_outreach_allowed')} | Historical Contact Status: {comp.get('historical_contact_status')}")
        print(f"Observations ({detail.get('observation_count', 0)}):")
        for o in detail.get("observations", []):
            print(f"  - [{o.get('observation_id')}] {o.get('source_name')} ({o.get('discovery_mode')}): {o.get('observed_at')}")
        return 0

    elif args.command == "qualify":
        cand = store.get_by_id(args.id)
        if not cand:
            print(f"Error: Candidate {args.id} not found", file=sys.stderr)
            return 1
        obs = store.get_observations_for_company(args.id)
        ev = store.get_evidence(args.id)
        qual = classifier.classify_company(cand, observations=obs, evidence=ev)
        store.save_candidate(cand)
        print(f"Candidate {args.id} qualification result:")
        print(f"  Outcome: {qual.outcome.value}")
        print(f"  Tier: {qual.company_tier.value}")
        print(f"  BIM Maturity: {qual.bim_maturity.value}")
        print(f"  Relationship Type: {qual.relationship_type.value}")
        print(f"  Cold Outreach Allowed: {qual.cold_outreach_allowed}")
        print(f"  Reasons: {qual.reasons}")
        return 0

    elif args.command == "watchlist":
        entries = bridge.get_watchlist_summary()
        print(f"Watchlist contains {len(entries)} candidate(s):")
        for w in entries:
            print(f"- [{w.get('company_id')}] {w.get('company_name')} ({w.get('canonical_domain')}): reason='{w.get('reason')}' status={w.get('status')}")
        return 0

    elif args.command == "watch":
        cand = store.get_by_id(args.id)
        if not cand:
            print(f"Error: Candidate {args.id} not found", file=sys.stderr)
            return 1
        entry = store.add_to_watchlist(args.id, reason=args.reason)
        print(f"Added {cand.name} to watchlist: status={entry.watch_status}")
        return 0

    elif args.command == "import-history":
        path = Path(args.file)
        if not path.exists():
            print(f"Error: File {path} not found", file=sys.stderr)
            return 1
        importer = HistoricalImporter(store=store)
        if path.suffix.lower() == ".csv":
            records = importer.parse_csv(path)
        elif path.suffix.lower() in (".xlsx", ".xlsm"):
            records = importer.parse_xlsx(path)
        else:
            print("Error: Only CSV and XLSX files are supported", file=sys.stderr)
            return 1
        res = importer.import_records(records)
        print(f"Imported from {path.name}:")
        print(f"  Rows read: {res.total_rows_read}")
        print(f"  Companies created: {res.companies_created}")
        print(f"  Companies updated: {res.companies_updated}")
        print(f"  Contacts extracted: {res.contacts_extracted}")
        print(f"  Duplicates skipped: {res.duplicates_skipped}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
