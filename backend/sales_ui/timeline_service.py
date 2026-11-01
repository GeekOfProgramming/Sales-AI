"""
Timeline aggregation service for Phase 10.5 Local Sales Operator Console.
Strictly aggregates existing historical records, observations, evidence, and audit events.
Does not mutate or fabricate audit history.
"""

import json
from typing import List, Optional, Dict, Any
from backend.sales_ui.view_models import CompanyTimelineEvent
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore


class TimelineService:
    def __init__(self, candidate_store: CandidateStore, review_store: ReviewStore):
        self.candidate_store = candidate_store
        self.review_store = review_store

    def get_company_timeline(self, candidate_id: str) -> List[CompanyTimelineEvent]:
        """Aggregate chronological timeline for a specific candidate company."""
        events: List[CompanyTimelineEvent] = []

        candidate = self.candidate_store.get_by_id(candidate_id)
        if not candidate:
            return events

        company_name = getattr(candidate, "company_name", None) or getattr(candidate, "name", "Company")

        # 1. First Seen / Discovered event
        if candidate.first_seen_at:
            events.append(
                CompanyTimelineEvent(
                    event_id=f"evt_seen_{candidate_id}",
                    candidate_id=candidate_id,
                    company_name=company_name,
                    event_type="discovered",
                    occurred_at=candidate.first_seen_at,
                    title="Company Discovered",
                    summary=f"First identified through discovery pipeline ({candidate.country or 'Global'}).",
                    actor="system",
                )
            )

        # 2. Researched event
        if candidate.last_researched_at:
            events.append(
                CompanyTimelineEvent(
                    event_id=f"evt_res_{candidate_id}",
                    candidate_id=candidate_id,
                    company_name=company_name,
                    event_type="researched",
                    occurred_at=candidate.last_researched_at,
                    title="Deep Research Performed",
                    summary=f"Analyzed company profile, BIM maturity ({candidate.bim_maturity or 'Unknown'}), and tier ({candidate.company_tier or 'Unassigned'}).",
                    actor="system",
                )
            )

        # 3. Discovery Observations
        observations = self.candidate_store.get_observations_for_company(candidate_id)
        for obs in observations:
            obs_time = obs.observed_at or obs.fetched_at or candidate.first_seen_at or ""
            events.append(
                CompanyTimelineEvent(
                    event_id=f"evt_obs_{obs.observation_id}",
                    candidate_id=candidate_id,
                    company_name=company_name,
                    event_type="discovered",
                    occurred_at=obs_time,
                    title=f"Observed on {obs.source_name or obs.source_type}",
                    summary=obs.raw_title or obs.raw_snippet or f"Observation from {obs.source_name}",
                    source_ref=obs.source_url or obs.source_id,
                    entity_ref=obs.observation_id,
                    actor="crawler",
                    safe_metadata={"source_type": obs.source_type, "discovery_mode": obs.discovery_mode},
                )
            )

        # 4. Market Evidence
        evidence_items = self.candidate_store.get_evidence(candidate_id)
        for ev in evidence_items:
            ev_time = ev.observed_at or candidate.first_seen_at or ""
            events.append(
                CompanyTimelineEvent(
                    event_id=f"evt_ev_{ev.evidence_id}",
                    candidate_id=candidate_id,
                    company_name=company_name,
                    event_type="signal_found",
                    occurred_at=ev_time,
                    title=f"Signal: {ev.category} ({ev.signal_type})",
                    summary=ev.text,
                    source_ref=ev.source_url,
                    entity_ref=ev.evidence_id,
                    actor="analyzer",
                    safe_metadata={"confidence": ev.confidence, "freshness": ev.freshness_status},
                )
            )

        # 5. Qualification
        if candidate.qualification_outcome:
            qual_time = candidate.last_seen_at or candidate.first_seen_at or ""
            events.append(
                CompanyTimelineEvent(
                    event_id=f"evt_qual_{candidate_id}",
                    candidate_id=candidate_id,
                    company_name=company_name,
                    event_type="qualified",
                    occurred_at=qual_time,
                    title=f"Qualification: {candidate.qualification_outcome}",
                    summary=f"Fit: {candidate.fit_assessment or 'N/A'}, Intent: {candidate.intent_assessment or 'N/A'}",
                    actor="system",
                )
            )

        # 6. Watchlist entry
        try:
            with self.candidate_store._get_connection() as conn:
                wl_row = conn.execute(
                    "SELECT * FROM watchlist WHERE candidate_id = ?",
                    (candidate_id,)
                ).fetchone()
                if wl_row:
                    wl_time = wl_row["last_checked_at"] or candidate.last_seen_at or ""
                    signals = json.loads(wl_row["signals_to_monitor_json"] or "[]")
                    events.append(
                        CompanyTimelineEvent(
                            event_id=f"evt_wl_{candidate_id}",
                            candidate_id=candidate_id,
                            company_name=company_name,
                            event_type="watchlisted",
                            occurred_at=wl_time,
                            title="Placed on Watchlist",
                            summary=f"Reason: {wl_row['watch_reason']}",
                            actor="operator",
                            safe_metadata={"signals": signals},
                        )
                    )
        except Exception:
            pass

        # 7. Historical Outreach Events
        historical_records = self.candidate_store.get_historical_outreach_for_candidate(candidate_id)
        for h in historical_records:
            h_time = h.get("contacted_at") or h.get("reply_at") or candidate.first_seen_at or ""
            # Contacted
            events.append(
                CompanyTimelineEvent(
                    event_id=f"evt_hist_{h.get('event_id', '')}",
                    candidate_id=candidate_id,
                    company_name=company_name,
                    event_type="historical_reply" if h.get("reply_status") else "contact_found",
                    occurred_at=h_time,
                    title=f"Historical Outreach: {h.get('contact_name', 'Contact')} ({h.get('channel', 'email')})",
                    summary=f"Status: {h.get('reply_status', 'Sent')}, Notes: {h.get('notes', 'None')}",
                    source_ref=h.get("source_ref"),
                    actor="historical_import",
                    safe_metadata={"meeting_status": h.get("meeting_status"), "reply_status": h.get("reply_status")},
                )
            )
            # Meeting
            if h.get("meeting_status"):
                events.append(
                    CompanyTimelineEvent(
                        event_id=f"evt_meet_{h.get('event_id', '')}",
                        candidate_id=candidate_id,
                        company_name=company_name,
                        event_type="meeting",
                        occurred_at=h.get("reply_at") or h_time,
                        title=f"Historical Meeting: {h.get('meeting_status')}",
                        summary=f"Outcome: {h.get('opportunity_status', 'Recorded')}",
                        actor="historical_import",
                    )
                )

        # 8. Draft review events & sends from Phase 9 (strictly isolated by candidate_id and candidate domain)
        try:
            cand_domain = getattr(candidate, "company_domain", None) or getattr(candidate, "domain", None)
            valid_lead_ids = [candidate_id]
            if cand_domain:
                valid_lead_ids.append(f"domain:{cand_domain.lower()}")
            placeholders = ", ".join("?" for _ in valid_lead_ids)

            with self.review_store._get_connection() as conn:
                query_params = [candidate_id] + valid_lead_ids
                drafts = conn.execute(
                    f"SELECT draft_id, revision, subject, approval_status, send_status, reviewer, created_at, approved_at, sent_at FROM drafts WHERE candidate_id = ? OR lead_id IN ({placeholders})",
                    query_params,
                ).fetchall()

                for d in drafts:
                    d_id = d["draft_id"]
                    rev = d["revision"]
                    # Draft created
                    events.append(
                        CompanyTimelineEvent(
                            event_id=f"evt_draft_{d_id}_r{rev}",
                            candidate_id=candidate_id,
                            company_name=company_name,
                            event_type="draft_created" if rev == 1 else "draft_edited",
                            occurred_at=d["created_at"],
                            title=f"Email Draft Revision {rev} Created",
                            summary=f"Subject: {d['subject']} (Status: {d['approval_status']})",
                            entity_ref=d_id,
                            revision=rev,
                            actor=d["reviewer"] or "system",
                        )
                    )
                    # Approved
                    if d["approved_at"]:
                        events.append(
                            CompanyTimelineEvent(
                                event_id=f"evt_app_{d_id}_r{rev}",
                                candidate_id=candidate_id,
                                company_name=company_name,
                                event_type="approved",
                                occurred_at=d["approved_at"],
                                title=f"Draft Approved (r{rev})",
                                summary=f"Explicitly approved by {d['reviewer'] or 'operator'}.",
                                entity_ref=d_id,
                                revision=rev,
                                actor=d["reviewer"] or "operator",
                            )
                        )
                    # Sent
                    if d["sent_at"]:
                        events.append(
                            CompanyTimelineEvent(
                                event_id=f"evt_sent_{d_id}_r{rev}",
                                candidate_id=candidate_id,
                                company_name=company_name,
                                event_type="sent",
                                occurred_at=d["sent_at"],
                                title=f"Email Sent (r{rev})",
                                summary=f"Sent subject '{d['subject']}'.",
                                entity_ref=d_id,
                                revision=rev,
                                actor=d["reviewer"] or "operator",
                            )
                        )

                # Send attempts (e.g. dry_run)
                attempts = conn.execute(
                    f"""SELECT sa.attempt_id, sa.draft_id, sa.revision, sa.status, sa.attempted_at, sa.provider 
                       FROM send_attempts sa 
                       JOIN drafts d ON sa.draft_id = d.draft_id AND sa.revision = d.revision 
                       WHERE d.candidate_id = ? OR d.lead_id IN ({placeholders})""",
                    query_params,
                ).fetchall()
                for a in attempts:
                    if a["status"] == "dry_run_success":
                        events.append(
                            CompanyTimelineEvent(
                                event_id=f"evt_dry_{a['attempt_id']}",
                                candidate_id=candidate_id,
                                company_name=company_name,
                                event_type="dry_run",
                                occurred_at=a["attempted_at"],
                                title=f"Dry Run Validation Passed (r{a['revision']})",
                                summary="Verified provider eligibility with 0 network calls.",
                                entity_ref=a["draft_id"],
                                revision=a["revision"],
                                actor="operator",
                            )
                        )
        except Exception:
            pass

        # Sort chronologically descending (most recent first)
        events.sort(key=lambda e: e.occurred_at or "", reverse=True)
        return events
