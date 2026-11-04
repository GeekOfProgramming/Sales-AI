"""
Dashboard aggregation service for Phase 10.5 Local Sales Operator Console.
Strictly read-only: aggregates live counts from CandidateStore and ReviewStore.
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from backend.sales_ui.view_models import DashboardSummary
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore


class DashboardService:
    def __init__(self, candidate_store: CandidateStore, review_store: ReviewStore):
        self.candidate_store = candidate_store
        self.review_store = review_store

    def get_summary(self) -> DashboardSummary:
        """Aggregate operational metrics across CandidateStore and ReviewStore."""
        from sales_engine.candidates.lifecycle_service import CandidateLifecycleService

        summary = DashboardSummary()

        # 1. Authoritative Candidate Lifecycle Queue Metrics (UI-02 workflow counters)
        try:
            lifecycle_svc = CandidateLifecycleService(self.candidate_store)
            q_counts = lifecycle_svc.compute_queue_counts()
            summary.active_candidates = q_counts.get("active", 0)
            summary.needs_research = q_counts.get("needs_research", 0)
            summary.needs_verification = q_counts.get("needs_verification", 0)
            summary.historical_pending = q_counts.get("historical_pending", 0)
            summary.qualified_leads = q_counts.get("qualified", 0)
            summary.new_candidates = q_counts.get("new", 0)
        except Exception:
            pass

        # 2. Candidate Store metrics
        try:
            with self.candidate_store._get_connection() as conn:
                # watchlist
                row = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()
                if row:
                    summary.watchlist = row[0]

                # historical_known_companies
                row = conn.execute(
                    """SELECT COUNT(*) FROM candidate_companies 
                       WHERE (historical_contact_status IS NOT NULL AND historical_contact_status != '') 
                          OR discovery_status IN ('known_company', 'reengagement_review')"""
                ).fetchone()
                if row:
                    summary.historical_known_companies = row[0]

                # contacts in historical_outreach_events
                row = conn.execute(
                    "SELECT COUNT(DISTINCT contact_email) FROM historical_outreach_events WHERE contact_email IS NOT NULL AND contact_email != ''"
                ).fetchone()
                if row:
                    summary.contacts_found = row[0]

                row = conn.execute(
                    """SELECT COUNT(DISTINCT contact_email) FROM historical_outreach_events 
                       WHERE contact_email IS NOT NULL AND contact_email != '' AND reply_status != 'bounced'"""
                ).fetchone()
                if row:
                    summary.usable_contacts = row[0]

                # historical positive replies
                row = conn.execute(
                    """SELECT COUNT(*) FROM historical_outreach_events 
                       WHERE reply_status IN ('positive', 'meeting_requested', 'interested', 'meeting_held')
                          OR meeting_status IN ('scheduled', 'completed')"""
                ).fetchone()
                if row:
                    summary.historical_positive_replies = row[0]

                # historical meetings
                row = conn.execute(
                    """SELECT COUNT(*) FROM historical_outreach_events 
                       WHERE meeting_status IS NOT NULL AND meeting_status != ''"""
                ).fetchone()
                if row:
                    summary.historical_meetings = row[0]
        except Exception:
            pass

        # 2. Review Store (Phase 9) metrics
        try:
            with self.review_store._get_connection() as conn:
                # drafts_pending_review: latest revision per draft_id
                cur = conn.execute("""
                    SELECT COUNT(*) FROM drafts d1
                    WHERE d1.revision = (
                        SELECT MAX(d2.revision) FROM drafts d2 WHERE d2.draft_id = d1.draft_id
                    )
                    AND d1.approval_status = 'pending_review'
                    AND d1.send_status != 'sent'
                """)
                row = cur.fetchone()
                if row:
                    summary.drafts_pending_review = row[0]

                # approved_not_sent
                cur = conn.execute("""
                    SELECT COUNT(*) FROM drafts d1
                    WHERE d1.revision = (
                        SELECT MAX(d2.revision) FROM drafts d2 WHERE d2.draft_id = d1.draft_id
                    )
                    AND d1.approval_status = 'approved'
                    AND d1.send_status != 'sent'
                """)
                row = cur.fetchone()
                if row:
                    summary.approved_not_sent = row[0]

                # suppressed count
                cur = conn.execute("SELECT COUNT(*) FROM suppression_list")
                row = cur.fetchone()
                if row:
                    summary.suppressed = row[0]

                # sent today & sent this week
                now = datetime.now(timezone.utc)
                today_str = now.strftime("%Y-%m-%d")
                week_ago_str = (now - timedelta(days=7)).isoformat()

                cur = conn.execute(
                    "SELECT COUNT(*) FROM send_attempts WHERE status = 'sent' AND attempted_at >= ?",
                    (today_str,),
                )
                row = cur.fetchone()
                if row:
                    summary.sent_today = row[0]

                cur = conn.execute(
                    "SELECT COUNT(*) FROM send_attempts WHERE status = 'sent' AND attempted_at >= ?",
                    (week_ago_str,),
                )
                row = cur.fetchone()
                if row:
                    summary.sent_this_week = row[0]
        except Exception:
            pass

        return summary
