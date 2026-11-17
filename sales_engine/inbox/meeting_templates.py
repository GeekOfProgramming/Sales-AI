"""
sales_engine/inbox/meeting_templates.py
MAIL-COMPOSE-01A-BRAND-ASSETS: pyBIM Meeting Email Templates Generator & Validator.

Integrates the four official pyBIM meeting email designs:
- pyBIM_Meeting_EN_Dark.html
- pyBIM_Meeting_EN_Light.html
- pyBIM_Meeting_IT_Dark.html
- pyBIM_Meeting_IT_Light.html

Faithfully implements parameter substitution matching pyBIM_Meeting_Studio.html:
- message_type: 'invitation' or 'confirmation'
- recipient_name: e.g. "Arch. Santoni"
- meeting_topic: e.g. "BIM Technical Coordination"
- meeting_date: e.g. "12 November 2026"
- meeting_time: e.g. "15:00"
- time_zone: default "Europe/Rome"
- platform: default "Google Meet"
- meeting_url: e.g. "https://meet.google.com/xxx-yyyy-zzz"
- agenda_items: list of 3 strings

Provides:
- Parameter validation (enforcing https:// link, checking required fields, detecting unresolved placeholders)
- Both branded HTML and clean plain-text fallback generation
- Local preview logo mapping (/static/brand/logo_white_transparent.png)
"""

import html
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

_MEETINGS_DIR = Path(__file__).resolve().parent / "brand_assets" / "meetings"

MEETING_TEMPLATES: Dict[str, str] = {
    "en_dark": (_MEETINGS_DIR / "pyBIM_Meeting_EN_Dark.html").read_text(encoding="utf-8") if (_MEETINGS_DIR / "pyBIM_Meeting_EN_Dark.html").exists() else "",
    "en_light": (_MEETINGS_DIR / "pyBIM_Meeting_EN_Light.html").read_text(encoding="utf-8") if (_MEETINGS_DIR / "pyBIM_Meeting_EN_Light.html").exists() else "",
    "it_dark": (_MEETINGS_DIR / "pyBIM_Meeting_IT_Dark.html").read_text(encoding="utf-8") if (_MEETINGS_DIR / "pyBIM_Meeting_IT_Dark.html").exists() else "",
    "it_light": (_MEETINGS_DIR / "pyBIM_Meeting_IT_Light.html").read_text(encoding="utf-8") if (_MEETINGS_DIR / "pyBIM_Meeting_IT_Light.html").exists() else "",
}

MEETING_METADATA: Dict[str, Any] = {
    "en": {
        "intro_invitation": "We would like to invite you to a video meeting to discuss <strong>[[TOPIC]]</strong>, review the key requirements and agree on the next steps.",
        "intro_confirmation": "Thank you for confirming the meeting. Below are the details for our discussion about <strong>[[TOPIC]]</strong> and the link to join.",
        "platform_default": "Google Meet",
        "date_placeholder": "[Meeting date]",
        "time_placeholder": "[HH:MM]",
        "recipient_placeholder": "[Recipient name]",
        "topic_placeholder": "[Meeting topic]",
        "agenda_defaults": [
            "Project scope and objectives",
            "Technical coordination and deliverables",
            "Next steps and responsibilities",
        ],
        "subject_invitation": "Meeting Invitation | pyBIM — ",
        "subject_confirmation": "Meeting Confirmation | pyBIM — ",
    },
    "it": {
        "intro_invitation": "desideriamo invitarLa a una videoriunione per discutere <strong>[[TOPIC]]</strong>, esaminare i requisiti principali e concordare i prossimi passi.",
        "intro_confirmation": "La ringraziamo per la conferma. Di seguito trova i dettagli della riunione dedicata a <strong>[[TOPIC]]</strong> e il collegamento per partecipare.",
        "platform_default": "Google Meet",
        "date_placeholder": "[Data della riunione]",
        "time_placeholder": "[HH:MM]",
        "recipient_placeholder": "[Nome destinatario]",
        "topic_placeholder": "[Argomento della riunione]",
        "agenda_defaults": [
            "Obiettivi e ambito del progetto",
            "Coordinamento tecnico e deliverable",
            "Prossime attività e responsabilità",
        ],
        "subject_invitation": "Invito alla riunione | pyBIM — ",
        "subject_confirmation": "Conferma riunione | pyBIM — ",
    },
}

TEMPLATE_CHOICES = [
    {"id": "standard", "label": "Standard Email (No Layout Template)"},
    {"id": "meeting_en_dark", "label": "Meeting — English / Dark Theme"},
    {"id": "meeting_en_light", "label": "Meeting — English / Light Theme"},
    {"id": "meeting_it_dark", "label": "Meeting — Italian / Dark Theme"},
    {"id": "meeting_it_light", "label": "Meeting — Italian / Light Theme"},
]


def _escape_html(s: Any) -> str:
    return html.escape(str(s) if s is not None else "", quote=True)


# Platform Configuration
PLATFORM_CONFIG = {
    "google_meet": {
        "id": "google_meet",
        "label": "Google Meet",
        "cta_en": "JOIN THE VIDEO CALL \u2197",
        "cta_it": "ACCEDI ALLA VIDEORIUNIONE \u2197",
        "placeholder": "https://meet.google.com/xxx-yyyy-zzz",
        "domain": "meet.google.com",
    },
    "ms_teams": {
        "id": "ms_teams",
        "label": "Microsoft Teams",
        "cta_en": "JOIN MICROSOFT TEAMS MEETING \u2197",
        "cta_it": "ACCEDI ALLA RIUNIONE TEAMS \u2197",
        "placeholder": "https://teams.microsoft.com/l/meetup-join/...",
        "domain": "teams.microsoft.com",
    },
    "zoom": {
        "id": "zoom",
        "label": "Zoom",
        "cta_en": "JOIN ZOOM MEETING \u2197",
        "cta_it": "ACCEDI ALLA RIUNIONE ZOOM \u2197",
        "placeholder": "https://us02web.zoom.us/j/...",
        "domain": "zoom.us",
    },
    "custom": {
        "id": "custom",
        "label": "Other / Custom Platform",
        "cta_en": "JOIN THE VIDEO CALL \u2197",
        "cta_it": "ACCEDI ALLA VIDEORIUNIONE \u2197",
        "placeholder": "https://...",
        "domain": None,
    },
}

def get_platform_info(platform_id: Optional[str], custom_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns resolved platform metadata (label, CTA text, placeholder).
    """
    clean_id = (platform_id or "").strip().lower()
    # Support direct labels or IDs
    if "teams" in clean_id:
        p = dict(PLATFORM_CONFIG["ms_teams"])
    elif "zoom" in clean_id:
        p = dict(PLATFORM_CONFIG["zoom"])
    elif "custom" in clean_id or "other" in clean_id:
        p = dict(PLATFORM_CONFIG["custom"])
        if custom_name and custom_name.strip():
            c_name = custom_name.strip()
            p["label"] = c_name
            p["cta_en"] = f"JOIN {c_name.upper()} MEETING \u2197"
            p["cta_it"] = f"ACCEDI ALLA RIUNIONE {c_name.upper()} \u2197"
    else:
        p = dict(PLATFORM_CONFIG["google_meet"])
    return p


def validate_meeting_parameters(params: Dict[str, Any]) -> List[str]:
    """
    Validates meeting parameters for completeness and readiness.
    Returns list of error messages (empty list if fully valid).
    """
    errors = []
    topic = (params.get("topic") or "").strip()
    recipient = (params.get("recipient") or "").strip()
    date_val = (params.get("date") or "").strip()
    time_val = (params.get("time") or "").strip()
    link = (params.get("link") or "").strip()
    platform_id = (params.get("platform_id") or params.get("platform") or "google_meet").strip()

    if not topic or topic.startswith("["):
        errors.append("Meeting topic is required and cannot be a placeholder.")
    if not recipient or recipient.startswith("["):
        errors.append("Recipient name is required and cannot be a placeholder.")
    if not date_val or date_val.startswith("["):
        errors.append("Meeting date is required and cannot be a placeholder.")
    if not time_val or time_val.startswith("["):
        errors.append("Meeting time is required and cannot be a placeholder.")
    
    if not link:
        errors.append("Meeting URL is required.")
    else:
        if not (link.startswith("https://") or link.startswith("http://")):
            errors.append("Meeting URL must start with https://.")
        elif link.startswith("["):
            errors.append("Meeting URL cannot be a placeholder.")
        else:
            # Platform-specific URL check
            plat_info = get_platform_info(platform_id)
            if plat_info.get("domain"):
                dom = plat_info["domain"]
                if dom not in link.lower():
                    errors.append(f"Meeting URL does not match selected platform ({plat_info['label']}). Expected URL containing '{dom}'.")

    return errors


def build_meeting_email(
    template_id: str,
    message_type: str = "invitation",  # invitation or confirmation
    recipient_name: Optional[str] = None,
    meeting_topic: Optional[str] = None,
    meeting_date: Optional[str] = None,
    meeting_time: Optional[str] = None,
    time_zone: str = "Europe/Rome",
    platform: str = "Google Meet",
    platform_id: Optional[str] = None,
    custom_platform_name: Optional[str] = None,
    meeting_url: Optional[str] = None,
    agenda_1: Optional[str] = None,
    agenda_2: Optional[str] = None,
    agenda_3: Optional[str] = None,
    for_local_preview: bool = False,
) -> Dict[str, str]:
    """
    Generates subject, plain-text body, and full HTML for a meeting email.
    """
    # Normalize template id
    clean_id = (template_id or "").strip().lower().replace("meeting_", "")
    if clean_id not in MEETING_TEMPLATES:
        clean_id = "en_dark"

    lang = clean_id.split("_")[0]  # 'en' or 'it'
    meta = MEETING_METADATA.get(lang, MEETING_METADATA["en"])

    is_confirmation = (message_type or "").strip().lower() == "confirmation"

    # Resolve platform information
    plat_info = get_platform_info(platform_id or platform, custom_platform_name)
    eff_platform = plat_info["label"]
    cta_text = plat_info["cta_it"] if lang == "it" else plat_info["cta_en"]

    # Effective values with fallback to defaults/placeholders
    eff_topic = (meeting_topic or "").strip() or meta["topic_placeholder"]
    eff_recipient = (recipient_name or "").strip() or meta["recipient_placeholder"]
    eff_date = (meeting_date or "").strip() or meta["date_placeholder"]
    eff_time = (meeting_time or "").strip() or meta["time_placeholder"]
    eff_tz = (time_zone or "").strip() or "Europe/Rome"
    eff_link = (meeting_url or "").strip() or "#"
    eff_link_text = (meeting_url or "").strip() or (
        "[Meeting link]" if lang == "en" else "[Link della riunione]"
    )

    agenda_defaults = meta["agenda_defaults"]
    eff_a1 = (agenda_1 or "").strip() or agenda_defaults[0]
    eff_a2 = (agenda_2 or "").strip() or agenda_defaults[1]
    eff_a3 = (agenda_3 or "").strip() or agenda_defaults[2]

    # Subject line
    subj_prefix = meta["subject_confirmation"] if is_confirmation else meta["subject_invitation"]
    subject = f"{subj_prefix}{eff_topic}"

    # Intro text
    intro_template = meta["intro_confirmation"] if is_confirmation else meta["intro_invitation"]
    intro_html = intro_template.replace("[[TOPIC]]", _escape_html(eff_topic))
    intro_plain = intro_template.replace("<strong>", "").replace("</strong>", "").replace("[[TOPIC]]", eff_topic)

    # Base HTML template: use parameterized template strings matching pyBIM_Meeting_Studio.html
    raw_html = MEETING_TEMPLATES[clean_id]

    # Map placeholders from either Studio syntax ([[X]]) or static design syntax ([X])
    placeholders_map = {
        # Recipient
        "[[RECIPIENT]]": _escape_html(eff_recipient),
        "Dear [Recipient name],": f"Dear {_escape_html(eff_recipient)},",
        "Gentile [Nome destinatario],": f"Gentile {_escape_html(eff_recipient)},",
        # Topic
        "[[TOPIC]]": _escape_html(eff_topic),
        "[Meeting topic]": _escape_html(eff_topic),
        "[Argomento della riunione]": _escape_html(eff_topic),
        # Date & Time & Timezone
        "[[DATE]]": _escape_html(eff_date),
        "[Meeting date]": _escape_html(eff_date),
        "[Data della riunione]": _escape_html(eff_date),
        "[[TIME]]": _escape_html(eff_time),
        "[HH:MM]": _escape_html(eff_time),
        "[[TIMEZONE]]": _escape_html(eff_tz),
        "[[PLATFORM]]": _escape_html(eff_platform),
        # Agenda
        "[[AGENDA_1]]": _escape_html(eff_a1),
        "Project scope and objectives": _escape_html(eff_a1) if lang == "en" else "Project scope and objectives",
        "Obiettivi e ambito del progetto": _escape_html(eff_a1) if lang == "it" else "Obiettivi e ambito del progetto",
        "[[AGENDA_2]]": _escape_html(eff_a2),
        "Technical coordination and deliverables": _escape_html(eff_a2) if lang == "en" else "Technical coordination and deliverables",
        "Coordinamento tecnico e deliverable": _escape_html(eff_a2) if lang == "it" else "Coordinamento tecnico e deliverable",
        "[[AGENDA_3]]": _escape_html(eff_a3),
        "Next steps and responsibilities": _escape_html(eff_a3) if lang == "en" else "Next steps and responsibilities",
        "Prossime attività e responsabilità": _escape_html(eff_a3) if lang == "it" else "Prossime attività e responsabilità",
        # Link
        "[[LINK_TEXT]]": _escape_html(eff_link_text),
        "[Insert meeting link]": _escape_html(eff_link_text),
        "[Inserire il link della riunione]": _escape_html(eff_link_text),
        "[[LINK]]": _escape_html(eff_link),
        'href="#"': f'href="{_escape_html(eff_link)}"',
    }

    full_html = raw_html

    # Replace intro line if confirmation or custom topic
    if is_confirmation:
        full_html = full_html.replace(
            "We would like to invite you to a video meeting to discuss <strong>[Meeting topic]</strong>, review the key requirements and agree on the next steps.",
            intro_html,
        ).replace(
            "desideriamo invitarLa a una videoriunione per discutere <strong>[Argomento della riunione]</strong>, esaminare i requisiti principali e concordare i prossimi passi.",
            intro_html,
        )

    for ph, val in placeholders_map.items():
        full_html = full_html.replace(ph, val)

    # Replace CTA button text according to platform
    if lang == "it":
        full_html = full_html.replace("ACCEDI ALLA VIDEORIUNIONE &nbsp;&#8599;", f"{cta_text} &nbsp;&#8599;")
        full_html = full_html.replace("ACCEDI ALLA VIDEORIUNIONE &nbsp;↗", f"{cta_text} &nbsp;↗")
    else:
        full_html = full_html.replace("JOIN THE VIDEO CALL &nbsp;&#8599;", f"{cta_text} &nbsp;&#8599;")
        full_html = full_html.replace("JOIN THE VIDEO CALL &nbsp;↗", f"{cta_text} &nbsp;↗")

    # Local preview logo rewriting
    if for_local_preview:
        full_html = full_html.replace(
            "https://www.pybim.com/_next/image?url=%2Flogo_white_transparent.png&amp;w=384&amp;q=75",
            "/static/brand/logo_white_transparent.png",
        )
        full_html = full_html.replace(
            "https://www.pybim.com/_next/image?url=%2Flogo_white_transparent.png&w=384&q=75",
            "/static/brand/logo_white_transparent.png",
        )
        full_html = full_html.replace(
            "https://www.pybim.com/logo_white_transparent.png",
            "/static/brand/logo_white_transparent.png",
        )

    # Plain text construction
    if lang == "it":
        salutation = f"Gentile {eff_recipient},"
        signoff = "Cordiali saluti,\nIl team pyBIM"
        details_block = (
            f"RIUNIONE: {eff_topic}\n"
            f"DATA: {eff_date}\n"
            f"ORA: {eff_time} ({eff_tz})\n"
            f"PIATTAFORMA: {eff_platform}\n"
            f"COLLEGAMENTO: {eff_link_text}"
        )
        agenda_block = (
            "PUNTI ALL'ORDINE DEL GIORNO:\n"
            f"01. {eff_a1}\n"
            f"02. {eff_a2}\n"
            f"03. {eff_a3}"
        )
        footer_block = "pyBIM / Tech-Enabled BIM Services\ninfo@pybim.it | https://www.pybim.it/it"
    else:
        salutation = f"Dear {eff_recipient},"
        signoff = "Kind regards,\nThe pyBIM Team"
        details_block = (
            f"MEETING: {eff_topic}\n"
            f"DATE: {eff_date}\n"
            f"TIME: {eff_time} ({eff_tz})\n"
            f"PLATFORM: {eff_platform}\n"
            f"LINK: {eff_link_text}"
        )
        agenda_block = (
            "DISCUSSION POINTS:\n"
            f"01. {eff_a1}\n"
            f"02. {eff_a2}\n"
            f"03. {eff_a3}"
        )
        footer_block = "pyBIM / Tech-Enabled BIM Services\ninfo@pybim.com | https://www.pybim.com/en"

    plain_body = (
        f"{salutation}\n\n"
        f"{intro_plain}\n\n"
        f"{details_block}\n\n"
        f"{agenda_block}\n\n"
        f"{signoff}\n\n"
        f"{footer_block}"
    )

    return {
        "subject": subject,
        "body_plain_text": plain_body,
        "body_html": full_html,
        "has_placeholders": (
            "[" in eff_topic
            or "[" in eff_recipient
            or "[" in eff_date
            or "[" in eff_time
            or eff_link == "#"
            or "[" in eff_link_text
        ),
    }
