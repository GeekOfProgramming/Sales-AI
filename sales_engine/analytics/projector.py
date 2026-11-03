# sales_engine/analytics/projector.py
"""
AnalyticsProjector: Idempotent projection from authoritative operational stores
(Phase 9 Review/Sending Store, Phase 10 Candidate Store, Phase 11 Inbox Store)
into the append-only AnalyticsEvent layer.

Strict Invariants:
1. Re-running projection is 100% idempotent; no duplicated events.
2. Operational stores are read-only; ZERO mutations to operational tables.
3. Historical and live data provenance are preserved and distinguishable.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

from sales_engine.analytics.models import (
    AnalyticsEvent,
    EventType,
    RelationshipType,
    OpportunityType,
)
from sales_engine.analytics.store import AnalyticsStore


class AnalyticsProjector:
    def __init__(
        self,
        analytics_store: AnalyticsStore,
        db_path: Optional[str] = None,
    ):
        self.analytics_store = analytics_store
        self.db_path = db_path or analytics_store.db_path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def run_projection(self) -> Dict[str, int]:
        """
        Executes a full idempotent projection from operational tables into analytics_events.
        Returns a breakdown of events projected.
        """
        stats = {
            "candidate_discovered": 0,
            "candidate_qualified": 0,
            "candidate_watchlisted": 0,
            "candidate_rejected": 0,
            "contact_selected": 0,
            "draft_generated": 0,
            "draft_approved": 0,
            "draft_rejected": 0,
            "draft_changed": 0,
            "email_sent": 0,
            "email_failed": 0,
            "email_bounced": 0,
            "reply_received": 0,
            "reply_classified": 0,
            "positive_interest": 0,
            "meeting_proposed": 0,
            "meeting_scheduled": 0,
            "meeting_completed": 0,
            "meeting_cancelled": 0,
            "opportunity_qualified": 0,
            "opportunity_closed_won": 0,
            "opportunity_closed_lost": 0,
            "suppressed": 0,
            "unsubscribed": 0,
            "total_new_events": 0,
        }

        # Cache company metadata (domain, market, source, relationship_type, etc.)
        company_meta = self._load_company_metadata()

        new_events: List[AnalyticsEvent] = []

        # 1. Project Candidate Store records
        cand_events = self._project_candidates(company_meta)
        new_events.extend(cand_events)

        # 2. Project Draft & Send attempts (Phase 9)
        draft_events = self._project_drafts_and_sends(company_meta)
        new_events.extend(draft_events)

        # 3. Project Inbox & Conversations (Phase 11)
        inbox_events = self._project_inbox(company_meta)
        new_events.extend(inbox_events)

        # 4. Project Suppression list
        supp_events = self._project_suppressions(company_meta)
        new_events.extend(supp_events)

        # Record events batch
        inserted = self.analytics_store.record_events_batch(new_events)
        stats["total_new_events"] = inserted

        for ev in new_events:
            if ev.event_type in stats:
                stats[ev.event_type] += 1

        # Update projection state
        self.analytics_store.update_projection_state(
            key="default",
            version="1.0",
            total_events=self.analytics_store.count_events(),
        )

        return stats

    def rebuild_projection(self) -> Dict[str, int]:
        """Clears existing analytics_events and repopulates idempotently."""
        self.analytics_store.clear_events()
        return self.run_projection()

    # -------------------------------------------------------------
    # Metadata Cache
    # -------------------------------------------------------------
    def _load_company_metadata(self) -> Dict[str, Dict[str, Any]]:
        meta: Dict[str, Dict[str, Any]] = {}
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_companies'")
                if not cur.fetchone():
                    return meta

                rows = conn.execute("SELECT * FROM candidate_companies").fetchall()

                for r in rows:
                    cid = r["candidate_id"]
                    sources = []
                    raw_src = r["source_refs_json"] if "source_refs_json" in r.keys() else None
                    if not raw_src and "sources_discovered_json" in r.keys():
                        raw_src = r["sources_discovered_json"]
                    if raw_src:
                        try:
                            sources = json.loads(raw_src)
                        except Exception:
                            sources = []
                    primary_src = sources[0] if sources else "unknown"

                    opp_types = []
                    if "opportunity_types_json" in r.keys() and r["opportunity_types_json"]:
                        try:
                            opp_types = json.loads(r["opportunity_types_json"])
                        except Exception:
                            opp_types = []

                    meta[cid] = {
                        "company_name": r["company_name"],
                        "domain": r["company_domain"],
                        "country": r["country"] or "unknown",
                        "company_tier": r["company_tier"],
                        "bim_maturity": r["bim_maturity"],
                        "relationship_type": r["relationship_type"] or "unknown",
                        "opportunity_types": opp_types,
                        "primary_source": primary_src,
                    }
        except Exception:
            pass
        return meta

    # -------------------------------------------------------------
    # 1. Candidates Projection
    # -------------------------------------------------------------
    def _project_candidates(self, company_meta: Dict[str, Dict[str, Any]]) -> List[AnalyticsEvent]:
        events: List[AnalyticsEvent] = []
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_companies'")
                if not cur.fetchone():
                    return events

                rows = conn.execute("SELECT * FROM candidate_companies").fetchall()

                for r in rows:
                    cid = r["candidate_id"]
                    m = company_meta.get(cid, {})
                    prov = "live_system"
                    src = m.get("primary_source", "unknown")
                    if "historical" in str(src).lower() or "import" in str(src).lower():
                        prov = "historical_import"

                    opp_types = m.get("opportunity_types", [])
                    ts = r["first_seen_at"] if "first_seen_at" in r.keys() and r["first_seen_at"] else None
                    if not ts and "created_at" in r.keys():
                        ts = r["created_at"]
                    if not ts:
                        ts = datetime.now(timezone.utc).isoformat()

                    updated_ts = r["last_seen_at"] if "last_seen_at" in r.keys() and r["last_seen_at"] else ts

                    # candidate_discovered
                    events.append(AnalyticsEvent(
                        event_id=f"evt_cand_disc_{cid}",
                        event_type=EventType.CANDIDATE_DISCOVERED.value,
                        occurred_at=ts,
                        company_id=cid,
                        candidate_id=cid,
                        market_id=m.get("country", "unknown"),
                        source_id=src,
                        relationship_type=m.get("relationship_type", "unknown"),
                        opportunity_types=opp_types,
                        company_tier=r["company_tier"],
                        bim_maturity=r["bim_maturity"],
                        recipient_domain=r["company_domain"],
                        provenance=prov,
                    ))

                    # Qualification status
                    status_val = ""
                    if "qualification_outcome" in r.keys() and r["qualification_outcome"]:
                        status_val = str(r["qualification_outcome"]).lower()
                    if not status_val and "discovery_status" in r.keys() and r["discovery_status"]:
                        status_val = str(r["discovery_status"]).lower()

                    if status_val in ("qualified", "qual", "qualified_for_enrichment"):
                        events.append(AnalyticsEvent(
                            event_id=f"evt_cand_qual_{cid}",
                            event_type=EventType.CANDIDATE_QUALIFIED.value,
                            occurred_at=updated_ts,
                            company_id=cid,
                            candidate_id=cid,
                            market_id=m.get("country", "unknown"),
                            source_id=src,
                            relationship_type=m.get("relationship_type", "unknown"),
                            opportunity_types=opp_types,
                            company_tier=r["company_tier"],
                            bim_maturity=r["bim_maturity"],
                            recipient_domain=r["company_domain"],
                            provenance=prov,
                        ))
                    elif status_val in ("watchlist", "watchlisted"):
                        events.append(AnalyticsEvent(
                            event_id=f"evt_cand_watch_{cid}",
                            event_type=EventType.CANDIDATE_WATCHLISTED.value,
                            occurred_at=updated_ts,
                            company_id=cid,
                            candidate_id=cid,
                            market_id=m.get("country", "unknown"),
                            source_id=src,
                            relationship_type=m.get("relationship_type", "unknown"),
                            opportunity_types=opp_types,
                            company_tier=r["company_tier"],
                            bim_maturity=r["bim_maturity"],
                            recipient_domain=r["company_domain"],
                            provenance=prov,
                        ))
                    elif status_val in ("disqualified", "rejected", "reject"):
                        events.append(AnalyticsEvent(
                            event_id=f"evt_cand_rej_{cid}",
                            event_type=EventType.CANDIDATE_REJECTED.value,
                            occurred_at=updated_ts,
                            company_id=cid,
                            candidate_id=cid,
                            market_id=m.get("country", "unknown"),
                            source_id=src,
                            relationship_type=m.get("relationship_type", "unknown"),
                            opportunity_types=opp_types,
                            company_tier=r["company_tier"],
                            bim_maturity=r["bim_maturity"],
                            recipient_domain=r["company_domain"],
                            provenance=prov,
                        ))

                # Contacts from candidate_contacts table
                cur_cont = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_contacts'")
                if cur_cont.fetchone():
                    cont_rows = conn.execute("SELECT * FROM candidate_contacts").fetchall()
                    for c in cont_rows:
                        cont_id = c["contact_id"]
                        cid = c["candidate_id"]
                        m = company_meta.get(cid, {})
                        c_ts = c["created_at"] if "created_at" in c.keys() and c["created_at"] else datetime.now(timezone.utc).isoformat()
                        events.append(AnalyticsEvent(
                            event_id=f"evt_contact_{cid}_{cont_id}",
                            event_type=EventType.CONTACT_SELECTED.value,
                            occurred_at=c_ts,
                            company_id=cid,
                            candidate_id=cid,
                            contact_id=cont_id,
                            market_id=m.get("country", "unknown"),
                            source_id=m.get("primary_source", "unknown"),
                            relationship_type=m.get("relationship_type", "unknown"),
                            opportunity_types=m.get("opportunity_types", []),
                            company_tier=m.get("company_tier"),
                            bim_maturity=m.get("bim_maturity"),
                            metadata_json=json.dumps({
                                "role": c["job_title"] if "job_title" in c.keys() else "unknown",
                                "seniority": c["seniority"] if "seniority" in c.keys() else "unknown",
                            }),
                        ))
        except Exception:
            pass
        return events

    # -------------------------------------------------------------
    # 2. Drafts and Sends (Phase 9)
    # -------------------------------------------------------------
    def _project_drafts_and_sends(self, company_meta: Dict[str, Dict[str, Any]]) -> List[AnalyticsEvent]:
        events: List[AnalyticsEvent] = []
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='drafts'")
                if not cur.fetchone():
                    return events

                draft_rows = conn.execute("""
                    SELECT draft_id, revision, lead_id, contact_id, recipient_email,
                           sender_email, subject, body, service_used, language, tone,
                           prompt_version, generation_model, approval_status, send_status,
                           created_at, updated_at, approved_at, sent_at
                    FROM drafts
                """).fetchall()

                for d in draft_rows:
                    did = d["draft_id"]
                    rev = d["revision"]
                    cid = d["lead_id"]
                    m = company_meta.get(cid, {})

                    rec_domain = None
                    if d["recipient_email"] and "@" in d["recipient_email"]:
                        rec_domain = d["recipient_email"].split("@")[-1].lower()

                    meta_dict = {
                        "subject_length": len(d["subject"] or ""),
                        "body_word_count": len((d["body"] or "").split()),
                        "language": d["language"] or "en",
                        "tone": d["tone"] or "professional_concise",
                        "service_used": d["service_used"] or "unknown",
                    }

                    # draft_generated
                    events.append(AnalyticsEvent(
                        event_id=f"evt_draft_gen_{did}_{rev}",
                        event_type=EventType.DRAFT_GENERATED.value,
                        occurred_at=d["created_at"],
                        company_id=cid,
                        lead_id=cid,
                        contact_id=d["contact_id"],
                        draft_id=did,
                        draft_revision=rev,
                        market_id=m.get("country", "unknown"),
                        source_id=m.get("primary_source", "unknown"),
                        relationship_type=m.get("relationship_type", "unknown"),
                        opportunity_types=m.get("opportunity_types", []),
                        company_tier=m.get("company_tier"),
                        bim_maturity=m.get("bim_maturity"),
                        sender_email=d["sender_email"],
                        recipient_domain=rec_domain,
                        model_name=d["generation_model"],
                        model_version=d["generation_model"],
                        prompt_version=d["prompt_version"],
                        metadata_json=json.dumps(meta_dict),
                    ))

                    # revisions > 1
                    if rev > 1:
                        events.append(AnalyticsEvent(
                            event_id=f"evt_draft_chg_{did}_{rev}",
                            event_type=EventType.DRAFT_CHANGED.value,
                            occurred_at=d["updated_at"] or d["created_at"],
                            company_id=cid,
                            lead_id=cid,
                            contact_id=d["contact_id"],
                            draft_id=did,
                            draft_revision=rev,
                            market_id=m.get("country", "unknown"),
                            source_id=m.get("primary_source", "unknown"),
                            relationship_type=m.get("relationship_type", "unknown"),
                            opportunity_types=m.get("opportunity_types", []),
                            model_name=d["generation_model"],
                            prompt_version=d["prompt_version"],
                        ))

                    app_status = (d["approval_status"] or "").lower()
                    if app_status == "approved":
                        events.append(AnalyticsEvent(
                            event_id=f"evt_draft_app_{did}_{rev}",
                            event_type=EventType.DRAFT_APPROVED.value,
                            occurred_at=d["approved_at"] or d["updated_at"],
                            company_id=cid,
                            lead_id=cid,
                            contact_id=d["contact_id"],
                            draft_id=did,
                            draft_revision=rev,
                            market_id=m.get("country", "unknown"),
                            source_id=m.get("primary_source", "unknown"),
                            relationship_type=m.get("relationship_type", "unknown"),
                            opportunity_types=m.get("opportunity_types", []),
                            model_name=d["generation_model"],
                            prompt_version=d["prompt_version"],
                        ))
                    elif app_status == "rejected":
                        events.append(AnalyticsEvent(
                            event_id=f"evt_draft_rej_{did}_{rev}",
                            event_type=EventType.DRAFT_REJECTED.value,
                            occurred_at=d["updated_at"],
                            company_id=cid,
                            lead_id=cid,
                            contact_id=d["contact_id"],
                            draft_id=did,
                            draft_revision=rev,
                            market_id=m.get("country", "unknown"),
                            source_id=m.get("primary_source", "unknown"),
                            relationship_type=m.get("relationship_type", "unknown"),
                            opportunity_types=m.get("opportunity_types", []),
                            model_name=d["generation_model"],
                            prompt_version=d["prompt_version"],
                        ))

                # Send attempts
                cur_send = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='send_attempts'")
                if cur_send.fetchone():
                    send_rows = conn.execute("""
                        SELECT attempt_id, send_key, draft_id, revision, recipient_email,
                               sender_email, status, error_type, attempted_at, completed_at
                        FROM send_attempts
                    """).fetchall()

                    for s in send_rows:
                        att_id = s["attempt_id"]
                        did = s["draft_id"]
                        rev = s["revision"]

                        # Lookup company for draft
                        lead_id = None
                        model_name = None
                        rec_domain = None
                        if s["recipient_email"] and "@" in s["recipient_email"]:
                            rec_domain = s["recipient_email"].split("@")[-1].lower()

                        d_match = conn.execute(
                            "SELECT lead_id, generation_model, prompt_version FROM drafts WHERE draft_id = ? AND revision = ?",
                            (did, rev),
                        ).fetchone()
                        if d_match:
                            lead_id = d_match["lead_id"]
                            model_name = d_match["generation_model"]

                        m = company_meta.get(lead_id, {})
                        s_status = (s["status"] or "").lower()

                        if s_status in ("sent", "success"):
                            events.append(AnalyticsEvent(
                                event_id=f"evt_email_sent_{att_id}",
                                event_type=EventType.EMAIL_SENT.value,
                                occurred_at=s["completed_at"] or s["attempted_at"],
                                company_id=lead_id,
                                lead_id=lead_id,
                                draft_id=did,
                                draft_revision=rev,
                                send_attempt_id=att_id,
                                market_id=m.get("country", "unknown"),
                                source_id=m.get("primary_source", "unknown"),
                                relationship_type=m.get("relationship_type", "unknown"),
                                opportunity_types=m.get("opportunity_types", []),
                                sender_email=s["sender_email"],
                                recipient_domain=rec_domain,
                                model_name=model_name,
                            ))
                        elif s_status == "bounced":
                            events.append(AnalyticsEvent(
                                event_id=f"evt_email_bounced_{att_id}",
                                event_type=EventType.EMAIL_BOUNCED.value,
                                occurred_at=s["completed_at"] or s["attempted_at"],
                                company_id=lead_id,
                                lead_id=lead_id,
                                draft_id=did,
                                draft_revision=rev,
                                send_attempt_id=att_id,
                                market_id=m.get("country", "unknown"),
                                source_id=m.get("primary_source", "unknown"),
                                relationship_type=m.get("relationship_type", "unknown"),
                                opportunity_types=m.get("opportunity_types", []),
                                sender_email=s["sender_email"],
                                recipient_domain=rec_domain,
                            ))
                        elif s_status in ("failed", "error"):
                            events.append(AnalyticsEvent(
                                event_id=f"evt_email_failed_{att_id}",
                                event_type=EventType.EMAIL_FAILED.value,
                                occurred_at=s["completed_at"] or s["attempted_at"],
                                company_id=lead_id,
                                lead_id=lead_id,
                                draft_id=did,
                                draft_revision=rev,
                                send_attempt_id=att_id,
                                market_id=m.get("country", "unknown"),
                                source_id=m.get("primary_source", "unknown"),
                                relationship_type=m.get("relationship_type", "unknown"),
                                opportunity_types=m.get("opportunity_types", []),
                                sender_email=s["sender_email"],
                                recipient_domain=rec_domain,
                            ))
        except Exception:
            pass
        return events

    # -------------------------------------------------------------
    # 3. Inbox and Conversations (Phase 11)
    # -------------------------------------------------------------
    def _project_inbox(self, company_meta: Dict[str, Dict[str, Any]]) -> List[AnalyticsEvent]:
        events: List[AnalyticsEvent] = []
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inbound_messages'")
                if not cur.fetchone():
                    return events

                msg_rows = conn.execute("""
                    SELECT inbound_message_id, conversation_id, company_id, contact_id,
                           from_email, subject, received_at, classification,
                           is_auto_reply, is_bounce, is_unsubscribe
                    FROM inbound_messages
                """).fetchall()

                for m in msg_rows:
                    mid = m["inbound_message_id"]
                    cid = m["company_id"]
                    meta = company_meta.get(cid, {})
                    rec_domain = None
                    if m["from_email"] and "@" in m["from_email"]:
                        rec_domain = m["from_email"].split("@")[-1].lower()

                    # reply_received
                    events.append(AnalyticsEvent(
                        event_id=f"evt_reply_rec_{mid}",
                        event_type=EventType.REPLY_RECEIVED.value,
                        occurred_at=m["received_at"],
                        company_id=cid,
                        contact_id=m["contact_id"],
                        conversation_id=m["conversation_id"],
                        inbound_message_id=mid,
                        market_id=meta.get("country", "unknown"),
                        source_id=meta.get("primary_source", "unknown"),
                        relationship_type=meta.get("relationship_type", "unknown"),
                        opportunity_types=meta.get("opportunity_types", []),
                        recipient_domain=rec_domain,
                        metadata_json=json.dumps({
                            "classification": m["classification"],
                            "is_auto_reply": bool(m["is_auto_reply"]),
                            "is_bounce": bool(m["is_bounce"]),
                            "is_unsubscribe": bool(m["is_unsubscribe"]),
                        }),
                    ))

                    # reply_classified
                    cls = (m["classification"] or "").lower()
                    if cls and cls != "unknown":
                        events.append(AnalyticsEvent(
                            event_id=f"evt_reply_cls_{mid}",
                            event_type=EventType.REPLY_CLASSIFIED.value,
                            occurred_at=m["received_at"],
                            company_id=cid,
                            contact_id=m["contact_id"],
                            conversation_id=m["conversation_id"],
                            inbound_message_id=mid,
                            market_id=meta.get("country", "unknown"),
                            source_id=meta.get("primary_source", "unknown"),
                            relationship_type=meta.get("relationship_type", "unknown"),
                            opportunity_types=meta.get("opportunity_types", []),
                            metadata_json=json.dumps({"category": cls}),
                        ))

                    if cls == "positive_interest":
                        events.append(AnalyticsEvent(
                            event_id=f"evt_pos_int_{mid}",
                            event_type=EventType.POSITIVE_INTEREST.value,
                            occurred_at=m["received_at"],
                            company_id=cid,
                            contact_id=m["contact_id"],
                            conversation_id=m["conversation_id"],
                            inbound_message_id=mid,
                            market_id=meta.get("country", "unknown"),
                            source_id=meta.get("primary_source", "unknown"),
                            relationship_type=meta.get("relationship_type", "unknown"),
                            opportunity_types=meta.get("opportunity_types", []),
                        ))

                # Conversation events
                cur_cev = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_events'")
                if cur_cev.fetchone():
                    cev_rows = conn.execute("""
                        SELECT event_id, conversation_id, event_type, actor, details_json, created_at
                        FROM conversation_events
                    """).fetchall()

                    for ce in cev_rows:
                        eid = ce["event_id"]
                        conv_id = ce["conversation_id"]
                        etype = ce["event_type"].lower()

                        # Lookup company for conversation
                        conv_row = conn.execute(
                            "SELECT company_id FROM conversations WHERE conversation_id = ?",
                            (conv_id,),
                        ).fetchone()
                        cid = conv_row["company_id"] if conv_row else None
                        meta = company_meta.get(cid, {})

                        mapped_type = None
                        if etype == "meeting_proposed":
                            mapped_type = EventType.MEETING_PROPOSED.value
                        elif etype == "meeting_scheduled":
                            mapped_type = EventType.MEETING_SCHEDULED.value
                        elif etype == "meeting_completed":
                            mapped_type = EventType.MEETING_COMPLETED.value
                        elif etype == "meeting_cancelled":
                            mapped_type = EventType.MEETING_CANCELLED.value

                        if mapped_type:
                            events.append(AnalyticsEvent(
                                event_id=f"evt_cev_{eid}",
                                event_type=mapped_type,
                                occurred_at=ce["created_at"],
                                company_id=cid,
                                conversation_id=conv_id,
                                market_id=meta.get("country", "unknown"),
                                source_id=meta.get("primary_source", "unknown"),
                                relationship_type=meta.get("relationship_type", "unknown"),
                                opportunity_types=meta.get("opportunity_types", []),
                            ))

                # Conversation opportunity status
                cur_conv = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'")
                if cur_conv.fetchone():
                    c_rows = conn.execute("""
                        SELECT conversation_id, company_id, meeting_status, opportunity_status, created_at, updated_at
                        FROM conversations
                    """).fetchall()

                    for c in c_rows:
                        conv_id = c["conversation_id"]
                        cid = c["company_id"]
                        meta = company_meta.get(cid, {})
                        opp_st = (c["opportunity_status"] or "").lower()

                        if opp_st == "qualified_opportunity":
                            events.append(AnalyticsEvent(
                                event_id=f"evt_opp_qual_{conv_id}",
                                event_type=EventType.OPPORTUNITY_QUALIFIED.value,
                                occurred_at=c["updated_at"] or c["created_at"],
                                company_id=cid,
                                conversation_id=conv_id,
                                market_id=meta.get("country", "unknown"),
                                source_id=meta.get("primary_source", "unknown"),
                                relationship_type=meta.get("relationship_type", "unknown"),
                                opportunity_types=meta.get("opportunity_types", []),
                            ))
                        elif opp_st == "closed_won":
                            events.append(AnalyticsEvent(
                                event_id=f"evt_opp_won_{conv_id}",
                                event_type=EventType.OPPORTUNITY_CLOSED_WON.value,
                                occurred_at=c["updated_at"] or c["created_at"],
                                company_id=cid,
                                conversation_id=conv_id,
                                market_id=meta.get("country", "unknown"),
                                source_id=meta.get("primary_source", "unknown"),
                                relationship_type=meta.get("relationship_type", "unknown"),
                                opportunity_types=meta.get("opportunity_types", []),
                            ))
                        elif opp_st == "closed_lost":
                            events.append(AnalyticsEvent(
                                event_id=f"evt_opp_lost_{conv_id}",
                                event_type=EventType.OPPORTUNITY_CLOSED_LOST.value,
                                occurred_at=c["updated_at"] or c["created_at"],
                                company_id=cid,
                                conversation_id=conv_id,
                                market_id=meta.get("country", "unknown"),
                                source_id=meta.get("primary_source", "unknown"),
                                relationship_type=meta.get("relationship_type", "unknown"),
                                opportunity_types=meta.get("opportunity_types", []),
                            ))
        except Exception:
            pass
        return events

    # -------------------------------------------------------------
    # 4. Suppressions Projection
    # -------------------------------------------------------------
    def _project_suppressions(self, company_meta: Dict[str, Dict[str, Any]]) -> List[AnalyticsEvent]:
        events: List[AnalyticsEvent] = []
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='suppression_list'")
                if not cur.fetchone():
                    return events

                rows = conn.execute("""
                    SELECT suppression_id, email, company_domain, reason, source, created_at
                    FROM suppression_list
                """).fetchall()

                for r in rows:
                    sid = r["suppression_id"]
                    reason = (r["reason"] or "").lower()
                    etype = EventType.UNSUBSCRIBED.value if "unsubscribe" in reason else EventType.SUPPRESSED.value

                    events.append(AnalyticsEvent(
                        event_id=f"evt_supp_{sid}",
                        event_type=etype,
                        occurred_at=r["created_at"],
                        recipient_domain=r["company_domain"],
                        metadata_json=json.dumps({"reason": r["reason"], "source": r["source"]}),
                    ))
        except Exception:
            pass
        return events
