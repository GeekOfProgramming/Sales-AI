import re
from typing import List, Optional, Tuple
from sales_engine.inbox.models import MeetingProposal


_TIMEZONE_PATTERNS = [
    r"\b(CET|CEST|UTC|GMT|EST|EDT|PST|PDT|BST)\b",
]

_DATETIME_PATTERNS = [
    # English days + times: "Tuesday at 10am", "Monday 14:00", "tomorrow at 3pm"
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(?:\s+(?:at|around|ore)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?",
    r"\b(?:tomorrow|next week)\b(?:\s+(?:at|around)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?",
    # Italian days + times: "martedì alle 10", "giovedì ore 15:30", "domani mattina"
    r"\b(?:luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)\b(?:\s+(?:alle|ore)?\s*\d{1,2}(?::\d{2})?)?",
    r"\b(?:domani|settimana prossima)\b(?:\s+(?:alle|ore|mattina|pomeriggio)?\s*\d{1,2}(?::\d{2})?)?",
    # Explicit dates: "14/10", "15 October", "22nd at 11:00"
    r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?(?:\s+(?:at|ore|alle)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?",
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s+(?:at|ore)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?",
    # Standalone time: "10:00 am", "15:30", "3pm"
    r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b",
    r"\b\d{1,2}\s*(?:am|pm)\b",
]


class MeetingExtractor:
    """
    Extracts explicit candidate meeting times from inbound emails.
    Never invents absent times or timezones.
    """

    @classmethod
    def extract(cls, body_text: str) -> Tuple[List[str], Optional[str], Optional[str]]:
        """
        Returns (proposed_times, timezone, raw_snippet).
        If no explicit times exist, returns ([], None, None).
        """
        if not body_text:
            return [], None, None

        clean = body_text.strip()
        times_found: List[str] = []

        # Find explicit timezone
        found_tz: Optional[str] = None
        for tz_pat in _TIMEZONE_PATTERNS:
            m = re.search(tz_pat, clean, re.IGNORECASE)
            if m:
                found_tz = m.group(1).upper()
                break

        # Find dates/times
        for pat in _DATETIME_PATTERNS:
            for match in re.finditer(pat, clean, re.IGNORECASE):
                snippet = match.group(0).strip()
                if len(snippet) > 2 and snippet.lower() not in [t.lower() for t in times_found]:
                    times_found.append(snippet)

        raw_snippet = None
        if times_found:
            # Capture surrounding sentence
            sentences = re.split(r"[.\n;!?]", clean)
            for s in sentences:
                if any(t.lower() in s.lower() for t in times_found):
                    raw_snippet = s.strip()
                    break

        return times_found, found_tz, raw_snippet

    @classmethod
    def create_proposal(
        cls,
        conversation_id: str,
        inbound_message_id: str,
        body_text: str,
    ) -> Optional[MeetingProposal]:
        times, tz, snippet = cls.extract(body_text)
        if not times:
            return None
        return MeetingProposal(
            conversation_id=conversation_id,
            inbound_message_id=inbound_message_id,
            proposed_times=times,
            timezone=tz,
            raw_text_snippet=snippet,
            status="proposed",
        )
