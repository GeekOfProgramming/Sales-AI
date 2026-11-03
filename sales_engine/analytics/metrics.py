# sales_engine/analytics/metrics.py
"""
AnalyticsMetricsCalculator: Computes deterministic funnel metrics, conversion rates,
dimensional slices, and small-sample safety gates.

Strict Invariants:
1. Every rate exposes numerator, denominator, rate, sample_size, and sample_status.
2. Auto-replies and bounces are excluded from human reply rates.
3. Denominators are strictly valid; never dividing by unrelated populations.
4. Samples below MIN_SAMPLE_FOR_COMPARISON (10) are NEVER labeled 'best' or ranked as top performer.
5. Service buyer outcomes are kept strictly separate from partner outcomes.
6. Missing values remain 'unknown', never fabricated.
"""

from datetime import datetime, timezone, timedelta
import json
from typing import Dict, Any, List, Optional, Tuple, Set

from sales_engine.analytics.models import (
    AnalyticsEvent,
    EventType,
    RelationshipType,
    OpportunityType,
    MetricRate,
    MIN_SAMPLE_FOR_COMPARISON,
    MIN_SAMPLE_FOR_RECOMMENDATION,
)
from sales_engine.analytics.store import AnalyticsStore


def calculate_rate(numerator: int, denominator: int) -> MetricRate:
    """Calculates a safe metric rate with sample size safety flags."""
    sample_size = denominator
    rate_val = (numerator / denominator) if denominator > 0 else 0.0
    is_valid = sample_size >= MIN_SAMPLE_FOR_COMPARISON
    status_str = "valid" if is_valid else "insufficient_sample"

    return MetricRate(
        numerator=numerator,
        denominator=denominator,
        rate=round(rate_val, 4),
        sample_size=sample_size,
        sample_status=status_str,
        is_statistically_valid=is_valid,
    )


class AnalyticsMetricsCalculator:
    def __init__(self, analytics_store: AnalyticsStore):
        self.analytics_store = analytics_store

    def _filter_events_by_window(
        self,
        events: List[AnalyticsEvent],
        window: str = "all",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[AnalyticsEvent]:
        """Filters events according to time window."""
        if not events:
            return []

        now = datetime.now(timezone.utc)
        since_dt = None

        if window == "7d":
            since_dt = now - timedelta(days=7)
        elif window == "30d":
            since_dt = now - timedelta(days=30)
        elif window == "90d":
            since_dt = now - timedelta(days=90)
        if window == "custom" and start_date:
            try:
                since_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=timezone.utc)
            except Exception:
                since_dt = None

        until_dt = None
        if window == "custom" and end_date:
            try:
                until_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                if until_dt.tzinfo is None:
                    until_dt = until_dt.replace(tzinfo=timezone.utc)
            except Exception:
                until_dt = None

        filtered = []
        for e in events:
            try:
                e_dt = datetime.fromisoformat(e.occurred_at.replace("Z", "+00:00"))
            except Exception:
                continue

            if since_dt and e_dt < since_dt:
                continue
            if until_dt and e_dt > until_dt:
                continue
            filtered.append(e)

        return filtered

    # -------------------------------------------------------------
    # Canonical Outcome Funnel
    # -------------------------------------------------------------
    def get_funnel_summary(
        self,
        window: str = "all",
        relationship_type: Optional[str] = None,
        market_id: Optional[str] = None,
        source_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Computes the canonical outcome funnel:
        Candidates Discovered -> Qualified -> Contact Selected -> Draft Generated
        -> Draft Approved -> Sent -> Delivered -> Human Reply -> Positive Reply
        -> Meeting Proposed -> Meeting Scheduled -> Qualified Opportunity -> Closed Won / Lost
        """
        all_events = self.analytics_store.list_events(
            relationship_type=relationship_type,
            market_id=market_id,
            source_id=source_id,
            limit=50000,
        )
        events = self._filter_events_by_window(all_events, window=window, start_date=start_date, end_date=end_date)

        # Stage Counts (unique entity or event counts)
        # 1. Candidates Discovered (distinct candidate_ids)
        discovered_cands = {e.candidate_id for e in events if e.event_type == EventType.CANDIDATE_DISCOVERED.value and e.candidate_id}
        # 2. Qualified Candidates (distinct candidate_ids)
        qualified_cands = {e.candidate_id for e in events if e.event_type == EventType.CANDIDATE_QUALIFIED.value and e.candidate_id}
        # 3. Contacts Selected (distinct contact_ids or (company_id, contact_id))
        contacts_selected = {f"{e.company_id}_{e.contact_id}" for e in events if e.event_type == EventType.CONTACT_SELECTED.value and e.contact_id}
        # 4. Drafts Generated (distinct draft_id)
        drafts_generated = {e.draft_id for e in events if e.event_type == EventType.DRAFT_GENERATED.value and e.draft_id}
        # 5. Drafts Approved (distinct draft_id)
        drafts_approved = {e.draft_id for e in events if e.event_type == EventType.DRAFT_APPROVED.value and e.draft_id}
        # 6. Emails Sent (distinct send_attempt_id or event_id)
        emails_sent = {e.send_attempt_id or e.event_id for e in events if e.event_type == EventType.EMAIL_SENT.value}
        # Emails Failed & Bounced
        emails_failed = {e.send_attempt_id or e.event_id for e in events if e.event_type == EventType.EMAIL_FAILED.value}
        emails_bounced = {e.send_attempt_id or e.event_id for e in events if e.event_type == EventType.EMAIL_BOUNCED.value}
        # Delivered = Sent - Bounced (if recorded during send)
        delivered_count = max(0, len(emails_sent) - len(emails_bounced))

        # Inbound Replies
        all_replies = [e for e in events if e.event_type == EventType.REPLY_RECEIVED.value]
        # Distinguish human replies vs auto_reply vs bounce
        human_replies = []
        auto_replies = []
        inbound_bounces = []

        for r in all_replies:
            is_auto = False
            is_bounce = False
            if r.metadata_json:
                try:
                    meta = json.loads(r.metadata_json)
                    is_auto = meta.get("is_auto_reply", False)
                    is_bounce = meta.get("is_bounce", False)
                except Exception:
                    pass
            if is_bounce:
                inbound_bounces.append(r)
            elif is_auto:
                auto_replies.append(r)
            else:
                human_replies.append(r)

        # Positive Interest
        positive_replies = [e for e in events if e.event_type == EventType.POSITIVE_INTEREST.value]

        # Meetings
        meetings_proposed = {e.conversation_id or e.event_id for e in events if e.event_type == EventType.MEETING_PROPOSED.value}
        meetings_scheduled = {e.conversation_id or e.event_id for e in events if e.event_type == EventType.MEETING_SCHEDULED.value}
        meetings_completed = {e.conversation_id or e.event_id for e in events if e.event_type == EventType.MEETING_COMPLETED.value}

        # Opportunities
        opportunities_qualified = {e.conversation_id or e.event_id for e in events if e.event_type == EventType.OPPORTUNITY_QUALIFIED.value}
        closed_won = {e.conversation_id or e.event_id for e in events if e.event_type == EventType.OPPORTUNITY_CLOSED_WON.value}
        closed_lost = {e.conversation_id or e.event_id for e in events if e.event_type == EventType.OPPORTUNITY_CLOSED_LOST.value}

        # Conversion Rates
        # Qualification Rate = qualified_cands / discovered_cands
        qual_rate = calculate_rate(len(qualified_cands), len(discovered_cands))

        # Draft Approval Rate = drafts_approved / drafts_generated
        approval_rate = calculate_rate(len(drafts_approved), len(drafts_generated))

        # Send to Reply Rate = human_replies / emails_sent (bounces/auto-replies EXCLUDED)
        send_to_reply_rate = calculate_rate(len(human_replies), len(emails_sent))

        # Send to Positive Reply Rate = positive_replies / emails_sent
        send_to_positive_rate = calculate_rate(len(positive_replies), len(emails_sent))

        # Human Reply to Positive Rate = positive_replies / human_replies
        reply_to_positive_rate = calculate_rate(len(positive_replies), len(human_replies))

        # Reply to Meeting Proposed Rate = meetings_proposed / human_replies
        reply_to_meeting_prop_rate = calculate_rate(len(meetings_proposed), len(human_replies))

        # Meeting Proposed to Scheduled Rate = meetings_scheduled / meetings_proposed
        prop_to_scheduled_rate = calculate_rate(len(meetings_scheduled), len(meetings_proposed))

        # Send to Meeting Scheduled Rate = meetings_scheduled / emails_sent
        send_to_meeting_rate = calculate_rate(len(meetings_scheduled), len(emails_sent))

        # Meeting to Closed Won Rate = closed_won / meetings_scheduled
        meeting_to_won_rate = calculate_rate(len(closed_won), len(meetings_scheduled))

        return {
            "window": window,
            "filter_relationship_type": relationship_type or "all",
            "filter_market": market_id or "all",
            "filter_source": source_id or "all",
            "counts": {
                "candidates_discovered": len(discovered_cands),
                "candidates_qualified": len(qualified_cands),
                "contacts_selected": len(contacts_selected),
                "drafts_generated": len(drafts_generated),
                "drafts_approved": len(drafts_approved),
                "emails_sent": len(emails_sent),
                "emails_failed": len(emails_failed),
                "emails_bounced": len(emails_bounced),
                "emails_delivered": delivered_count,
                "total_replies_received": len(all_replies),
                "human_replies": len(human_replies),
                "auto_replies": len(auto_replies),
                "inbound_bounces": len(inbound_bounces),
                "positive_replies": len(positive_replies),
                "meetings_proposed": len(meetings_proposed),
                "meetings_scheduled": len(meetings_scheduled),
                "meetings_completed": len(meetings_completed),
                "opportunities_qualified": len(opportunities_qualified),
                "closed_won": len(closed_won),
                "closed_lost": len(closed_lost),
            },
            "rates": {
                "qualification_rate": qual_rate.to_dict(),
                "draft_approval_rate": approval_rate.to_dict(),
                "send_to_human_reply_rate": send_to_reply_rate.to_dict(),
                "send_to_positive_reply_rate": send_to_positive_rate.to_dict(),
                "human_reply_to_positive_rate": reply_to_positive_rate.to_dict(),
                "reply_to_meeting_proposed_rate": reply_to_meeting_prop_rate.to_dict(),
                "meeting_proposed_to_scheduled_rate": prop_to_scheduled_rate.to_dict(),
                "send_to_meeting_scheduled_rate": send_to_meeting_rate.to_dict(),
                "meeting_to_closed_won_rate": meeting_to_won_rate.to_dict(),
            },
        }

    # -------------------------------------------------------------
    # Slices by Dimensions: Source, Market, Role, Opportunity Type
    # -------------------------------------------------------------
    def get_source_metrics(self, window: str = "all") -> List[Dict[str, Any]]:
        """Computes per-source performance metrics with small-sample safety."""
        events = self._filter_events_by_window(self.analytics_store.list_events(limit=50000), window=window)

        # Group by source_id
        sources: Set[str] = {e.source_id or "unknown" for e in events}
        results = []

        for src in sorted(sources):
            src_ev = [e for e in events if (e.source_id or "unknown") == src]

            discovered = len({e.candidate_id for e in src_ev if e.event_type == EventType.CANDIDATE_DISCOVERED.value and e.candidate_id})
            qualified = len({e.candidate_id for e in src_ev if e.event_type == EventType.CANDIDATE_QUALIFIED.value and e.candidate_id})
            sent = len({e.send_attempt_id or e.event_id for e in src_ev if e.event_type == EventType.EMAIL_SENT.value})

            human_replies = [e for e in src_ev if e.event_type == EventType.REPLY_RECEIVED.value and not self._is_auto_or_bounce(e)]
            pos_replies = [e for e in src_ev if e.event_type == EventType.POSITIVE_INTEREST.value]
            meetings = len({e.conversation_id or e.event_id for e in src_ev if e.event_type == EventType.MEETING_SCHEDULED.value})
            opps = len({e.conversation_id or e.event_id for e in src_ev if e.event_type == EventType.OPPORTUNITY_QUALIFIED.value})

            qual_rate = calculate_rate(qualified, discovered)
            reply_rate = calculate_rate(len(human_replies), sent)
            pos_rate = calculate_rate(len(pos_replies), sent)
            meet_rate = calculate_rate(meetings, sent)

            # Sample safety verdict
            sample_size = sent if sent > 0 else discovered
            has_adequate_sample = sample_size >= MIN_SAMPLE_FOR_COMPARISON
            verdict = "insufficient_sample" if not has_adequate_sample else "comparable"

            results.append({
                "source_id": src,
                "discovered": discovered,
                "qualified": qualified,
                "qualification_rate": qual_rate.to_dict(),
                "emails_sent": sent,
                "human_replies": len(human_replies),
                "reply_rate": reply_rate.to_dict(),
                "positive_replies": len(pos_replies),
                "positive_rate": pos_rate.to_dict(),
                "meetings_scheduled": meetings,
                "meeting_rate": meet_rate.to_dict(),
                "opportunities": opps,
                "sample_size": sample_size,
                "status": verdict,
                "is_statistically_valid": has_adequate_sample,
            })

        return results

    def get_market_metrics(self, window: str = "all") -> List[Dict[str, Any]]:
        """Computes per-market (country) performance metrics with small-sample safety."""
        events = self._filter_events_by_window(self.analytics_store.list_events(limit=50000), window=window)

        markets: Set[str] = {e.market_id or "unknown" for e in events}
        results = []

        for mkt in sorted(markets):
            mkt_ev = [e for e in events if (e.market_id or "unknown") == mkt]

            discovered = len({e.candidate_id for e in mkt_ev if e.event_type == EventType.CANDIDATE_DISCOVERED.value and e.candidate_id})
            qualified = len({e.candidate_id for e in mkt_ev if e.event_type == EventType.CANDIDATE_QUALIFIED.value and e.candidate_id})
            sent = len({e.send_attempt_id or e.event_id for e in mkt_ev if e.event_type == EventType.EMAIL_SENT.value})

            human_replies = [e for e in mkt_ev if e.event_type == EventType.REPLY_RECEIVED.value and not self._is_auto_or_bounce(e)]
            pos_replies = [e for e in mkt_ev if e.event_type == EventType.POSITIVE_INTEREST.value]
            meetings = len({e.conversation_id or e.event_id for e in mkt_ev if e.event_type == EventType.MEETING_SCHEDULED.value})

            qual_rate = calculate_rate(qualified, discovered)
            reply_rate = calculate_rate(len(human_replies), sent)
            pos_rate = calculate_rate(len(pos_replies), sent)
            meet_rate = calculate_rate(meetings, sent)

            sample_size = sent if sent > 0 else discovered
            has_adequate_sample = sample_size >= MIN_SAMPLE_FOR_COMPARISON

            results.append({
                "market_id": mkt,
                "discovered": discovered,
                "qualified": qualified,
                "qualification_rate": qual_rate.to_dict(),
                "emails_sent": sent,
                "human_replies": len(human_replies),
                "reply_rate": reply_rate.to_dict(),
                "positive_replies": len(pos_replies),
                "positive_rate": pos_rate.to_dict(),
                "meetings_scheduled": meetings,
                "meeting_rate": meet_rate.to_dict(),
                "sample_size": sample_size,
                "status": "comparable" if has_adequate_sample else "insufficient_sample",
                "is_statistically_valid": has_adequate_sample,
            })

        return results

    def get_relationship_type_metrics(self, window: str = "all") -> List[Dict[str, Any]]:
        """
        Computes outcomes separated by relationship type:
        service_buyer, technology_partner, channel_partner, strategic_partner, mixed, unknown.
        CRITICAL: Never combines partner outcomes into direct service buyer success.
        """
        events = self._filter_events_by_window(self.analytics_store.list_events(limit=50000), window=window)

        types: Set[str] = {e.relationship_type or "unknown" for e in events}
        results = []

        for rtype in sorted(types):
            r_ev = [e for e in events if (e.relationship_type or "unknown") == rtype]

            discovered = len({e.candidate_id for e in r_ev if e.event_type == EventType.CANDIDATE_DISCOVERED.value and e.candidate_id})
            qualified = len({e.candidate_id for e in r_ev if e.event_type == EventType.CANDIDATE_QUALIFIED.value and e.candidate_id})
            sent = len({e.send_attempt_id or e.event_id for e in r_ev if e.event_type == EventType.EMAIL_SENT.value})

            human_replies = [e for e in r_ev if e.event_type == EventType.REPLY_RECEIVED.value and not self._is_auto_or_bounce(e)]
            pos_replies = [e for e in r_ev if e.event_type == EventType.POSITIVE_INTEREST.value]
            meetings = len({e.conversation_id or e.event_id for e in r_ev if e.event_type == EventType.MEETING_SCHEDULED.value})
            won = len({e.conversation_id or e.event_id for e in r_ev if e.event_type == EventType.OPPORTUNITY_CLOSED_WON.value})

            sample_size = sent if sent > 0 else discovered
            has_adequate_sample = sample_size >= MIN_SAMPLE_FOR_COMPARISON

            results.append({
                "relationship_type": rtype,
                "discovered": discovered,
                "qualified": qualified,
                "emails_sent": sent,
                "human_replies": len(human_replies),
                "reply_rate": calculate_rate(len(human_replies), sent).to_dict(),
                "positive_replies": len(pos_replies),
                "positive_rate": calculate_rate(len(pos_replies), sent).to_dict(),
                "meetings_scheduled": meetings,
                "closed_won": won,
                "sample_size": sample_size,
                "status": "comparable" if has_adequate_sample else "insufficient_sample",
                "is_statistically_valid": has_adequate_sample,
            })

        return results

    def get_contact_metrics(self, window: str = "all") -> List[Dict[str, Any]]:
        """Analyzes contact roles and seniority anonymously without sensitive PII."""
        events = self._filter_events_by_window(
            self.analytics_store.list_events(event_type=EventType.CONTACT_SELECTED.value, limit=50000),
            window=window,
        )
        role_map: Dict[str, int] = {}
        seniority_map: Dict[str, int] = {}

        for e in events:
            role = "unknown"
            sen = "unknown"
            if e.metadata_json:
                try:
                    m = json.loads(e.metadata_json)
                    role = m.get("role") or "unknown"
                    sen = m.get("seniority") or "unknown"
                except Exception:
                    pass
            role_map[role] = role_map.get(role, 0) + 1
            seniority_map[sen] = seniority_map.get(sen, 0) + 1

        return [
            {"dimension": "role", "breakdown": role_map},
            {"dimension": "seniority", "breakdown": seniority_map},
        ]

    def get_message_metrics(self, window: str = "all") -> Dict[str, Any]:
        """Analyzes safe message metrics: tone, language, prompt_version, average length."""
        events = self._filter_events_by_window(
            self.analytics_store.list_events(event_type=EventType.DRAFT_GENERATED.value, limit=50000),
            window=window,
        )
        tones: Dict[str, int] = {}
        languages: Dict[str, int] = {}
        prompts: Dict[str, int] = {}
        models: Dict[str, int] = {}
        total_sub_len = 0
        total_words = 0
        count = len(events)

        for e in events:
            if e.model_name:
                models[e.model_name] = models.get(e.model_name, 0) + 1
            if e.prompt_version:
                prompts[e.prompt_version] = prompts.get(e.prompt_version, 0) + 1

            if e.metadata_json:
                try:
                    m = json.loads(e.metadata_json)
                    tone = m.get("tone", "unknown")
                    lang = m.get("language", "unknown")
                    tones[tone] = tones.get(tone, 0) + 1
                    languages[lang] = languages.get(lang, 0) + 1
                    total_sub_len += m.get("subject_length", 0)
                    total_words += m.get("body_word_count", 0)
                except Exception:
                    pass

        avg_sub_len = (total_sub_len / count) if count > 0 else 0.0
        avg_body_words = (total_words / count) if count > 0 else 0.0

        return {
            "total_drafts": count,
            "tone_distribution": tones,
            "language_distribution": languages,
            "prompt_versions": prompts,
            "generation_models": models,
            "average_subject_length": round(avg_sub_len, 1),
            "average_body_word_count": round(avg_body_words, 1),
        }

    def get_reply_categories_metrics(self, window: str = "all") -> Dict[str, Any]:
        """Categorizes replies: positive_interest, meeting_proposed, referral, question, not_now, etc."""
        events = self._filter_events_by_window(
            self.analytics_store.list_events(event_type=EventType.REPLY_RECEIVED.value, limit=50000),
            window=window,
        )
        categories: Dict[str, int] = {}
        human_count = 0
        auto_count = 0
        bounce_count = 0

        for e in events:
            cat = "unknown"
            is_auto = False
            is_bounce = False
            if e.metadata_json:
                try:
                    m = json.loads(e.metadata_json)
                    cat = m.get("classification") or "unknown"
                    is_auto = m.get("is_auto_reply", False)
                    is_bounce = m.get("is_bounce", False)
                except Exception:
                    pass

            if is_bounce:
                bounce_count += 1
                cat = "bounce"
            elif is_auto:
                auto_count += 1
                cat = "auto_reply"
            else:
                human_count += 1

            categories[cat] = categories.get(cat, 0) + 1

        return {
            "total_replies": len(events),
            "human_replies": human_count,
            "auto_replies": auto_count,
            "bounces": bounce_count,
            "breakdown": categories,
        }

    # -------------------------------------------------------------
    # Helper
    # -------------------------------------------------------------
    def _is_auto_or_bounce(self, event: AnalyticsEvent) -> bool:
        if not event.metadata_json:
            return False
        try:
            m = json.loads(event.metadata_json)
            return bool(m.get("is_auto_reply") or m.get("is_bounce"))
        except Exception:
            return False
