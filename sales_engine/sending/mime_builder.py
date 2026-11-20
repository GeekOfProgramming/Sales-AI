"""
sales_engine/sending/mime_builder.py
MAIL-SEND-01A: Standards-Compliant RFC 5322 MIME Generator.

Constructs multipart/alternative or multipart/related MIME messages with:
- Correct From, Reply-To, To, CC, and Subject
- Strict BCC privacy: BCC addresses are part of envelope recipients but NEVER serialized into MIME headers
- Strict UTF-8 subject and body encoding
- Stable RFC 5322 Message-ID (reusable across retries/reconciliation)
- Account-specific official pyBIM corporate signatures
- Meeting template integration with selected platform
- Embedded CID logo (Content-ID: <pybim-logo>) without external CDN reliance
- Placeholder & meeting link safety guards
"""

import re
import os
import email.utils
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from sales_engine.inbox.signatures import (
    get_default_signature,
    strip_signature,
    strip_signature_html,
    _BRAND_ASSETS_DIR,
)

# Canonical CID for embedded pyBIM logo
PYBIM_LOGO_CID = "pybim-logo@pybim.com"
_LOGO_FILE_PATH = _BRAND_ASSETS_DIR / "signatures" / "assets" / "logo_white_transparent.png"

# Centralized authoritative sender-identity mapping: mailbox -> display name
SENDER_DISPLAY_NAMES: Dict[str, str] = {
    "info@pybim.com": "pyBIM",
    "info@pybim.it": "pyBIM",
}

DEFAULT_SENDER_DISPLAY_NAME = "pyBIM"


def format_sender_address(sender_email: str, display_name: Optional[str] = None) -> str:
    """
    Formats a standards-compliant RFC 5322 From header with display name.
    e.g., 'pyBIM <info@pybim.com>'
    """
    clean_email = (sender_email or "").strip().lower()
    name = display_name or SENDER_DISPLAY_NAMES.get(clean_email, DEFAULT_SENDER_DISPLAY_NAME)
    return email.utils.formataddr((name, clean_email))


UNRESOLVED_PLACEHOLDER_REGEX = re.compile(
    r"\[(Meeting topic|Meeting date|HH:MM|Recipient name|Meeting link|Argomento della riunione|Data della riunione|Nome destinatario|Inserire il link|Insert meeting link)\]",
    re.IGNORECASE,
)


def generate_rfc_message_id(domain: str = "pybim.com") -> str:
    """Generates a standards-compliant RFC 5322 Message-ID."""
    clean_domain = (domain or "pybim.com").split("@")[-1].strip()
    return email.utils.make_msgid(domain=clean_domain)


def validate_message_payload(
    subject: str,
    plain_text: str,
    html_text: Optional[str] = None,
) -> List[str]:
    """
    Scans subject and message bodies for unresolved template placeholders or invalid links.
    Returns list of error messages (empty if completely clean).
    """
    errors = []
    combined = f"{subject or ''}\n{plain_text or ''}\n{html_text or ''}"

    matches = UNRESOLVED_PLACEHOLDER_REGEX.findall(combined)
    if matches:
        unique_matches = sorted(list(set(matches)))
        errors.append(f"Unresolved placeholder(s) detected: {', '.join(unique_matches)}")

    # Reject raw bracketed placeholders like [Placeholder]
    bracket_matches = re.findall(r"\[[A-Za-z0-9 _\-\/]{3,30}\]", combined)
    if bracket_matches:
        suspicious = [m for m in bracket_matches if not m.startswith("[cid:") and not m.startswith("[image")]
        if suspicious:
            # Check if any look like obvious unresolved fields
            known_fields = [s for s in suspicious if any(k in s.lower() for k in ("meeting", "name", "date", "link", "topic", "argomento", "data", "orario"))]
            if known_fields:
                errors.append(f"Unresolved field placeholder(s) detected: {', '.join(set(known_fields))}")

    return errors


def build_mime_message(
    sender_email: str,
    to_addresses: List[str],
    subject: str,
    body_plain_text: str,
    body_html_sanitized: Optional[str] = None,
    cc_addresses: Optional[List[str]] = None,
    bcc_addresses: Optional[List[str]] = None,
    rfc_message_id: Optional[str] = None,
    reply_to: Optional[str] = None,
    account_id: Optional[str] = None,
    include_official_signature: bool = True,
    custom_headers: Optional[Dict[str, str]] = None,
) -> Tuple[EmailMessage, List[str]]:
    """
    Constructs a standards-compliant RFC 5322 MIME EmailMessage and returns (msg, envelope_recipients).
    
    Guarantees:
    1. BCC addresses are returned in envelope_recipients but NEVER added to msg headers.
    2. Logo image is embedded as inline CID (<pybim-logo@pybim.com>) if referenced in HTML.
    3. Official pyBIM signature is attached once without duplicate stacking.
    4. UTF-8 charset is preserved cleanly.
    5. Rejects messages with unresolved placeholders.
    """
    to_clean = [a.strip() for a in (to_addresses or []) if a and a.strip()]
    cc_clean = [a.strip() for a in (cc_addresses or []) if a and a.strip()]
    bcc_clean = [a.strip() for a in (bcc_addresses or []) if a and a.strip()]

    if not to_clean:
        raise ValueError("At least one To recipient is required.")
    if not sender_email or "@" not in sender_email:
        raise ValueError(f"Valid sender email is required: '{sender_email}'")

    # Envelope recipients = To + CC + BCC
    envelope_recipients = list(dict.fromkeys(to_clean + cc_clean + bcc_clean))

    # Safety checks
    validation_errors = validate_message_payload(
        subject=subject,
        plain_text=body_plain_text,
        html_text=body_html_sanitized,
    )
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    # Retrieve official signature
    sig_data = get_default_signature(account_id or sender_email)
    sig_plain = sig_data.get("plain", "")
    sig_html = sig_data.get("html", "")

    # Strip any user-body duplicate signature before assembling
    clean_plain = strip_signature(body_plain_text or "").rstrip()
    if include_official_signature and sig_plain:
        final_plain = f"{clean_plain}\n\n{sig_plain}"
    else:
        final_plain = clean_plain

    # Handle HTML and signature
    has_html = bool(body_html_sanitized and body_html_sanitized.strip())
    final_html = None
    if has_html:
        clean_html = strip_signature_html(body_html_sanitized or "").rstrip()
        if include_official_signature and sig_html:
            # If the body is a complete HTML document (e.g. meeting template), do not append duplicate table
            if "<html" in clean_html.lower() or "role=\"presentation\"" in clean_html.lower():
                final_html = clean_html
            else:
                final_html = f"{clean_html}<br><br>{sig_html}"
        else:
            final_html = clean_html

    # Replace remote logo references with CID in HTML
    embedded_logo = False
    if final_html:
        # Check for remote or local logo URLs and rewrite to cid:
        logo_patterns = [
            "https://www.pybim.com/logo_white_transparent.png",
            "/static/brand/logo_white_transparent.png",
            "https://www.pybim.com/_next/image?url=%2Flogo_white_transparent.png&amp;w=384&amp;q=75",
            "https://www.pybim.com/_next/image?url=%2Flogo_white_transparent.png&w=384&q=75",
        ]
        for pat in logo_patterns:
            if pat in final_html:
                final_html = final_html.replace(pat, f"cid:{PYBIM_LOGO_CID}")
                embedded_logo = True

    # Construct EmailMessage
    msg = EmailMessage()
    msg["Subject"] = subject or "(No Subject)"
    msg["From"] = format_sender_address(sender_email)
    msg["To"] = ", ".join(to_clean)
    if cc_clean:
        msg["Cc"] = ", ".join(cc_clean)
    # CRITICAL: Never add msg["Bcc"]! Envelope delivery handles BCC privately.

    if reply_to:
        msg["Reply-To"] = reply_to
    else:
        msg["Reply-To"] = sender_email

    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = rfc_message_id or generate_rfc_message_id(sender_email)

    if custom_headers:
        for k, v in custom_headers.items():
            if k.lower() not in ("from", "to", "cc", "bcc", "subject", "message-id"):
                msg[k] = v

    # Build MIME content
    if final_html:
        # multipart/alternative with text and html
        msg.set_content(final_plain, charset="utf-8")
        msg.add_alternative(final_html, subtype="html", charset="utf-8")

        # If logo CID was referenced and logo file exists, attach inline image
        if (embedded_logo or f"cid:{PYBIM_LOGO_CID}" in final_html) and _LOGO_FILE_PATH.exists():
            logo_data = _LOGO_FILE_PATH.read_bytes()
            # Attach to the html part (related)
            html_part = msg.get_payload()[-1] if isinstance(msg.get_payload(), list) else msg
            html_part.add_related(
                logo_data,
                maintype="image",
                subtype="png",
                cid=f"<{PYBIM_LOGO_CID}>",
                filename="logo_white_transparent.png",
                disposition="inline",
            )
    else:
        # Pure plain text
        msg.set_content(final_plain, charset="utf-8")

    return msg, envelope_recipients
