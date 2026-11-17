"""
sales_engine/inbox/signatures.py
MAIL-COMPOSE-01A-BRAND-ASSETS: Official pyBIM Email Signatures Integration.

Preserves the authoritative HTML table layout, inline styling, contact data,
mobile/WhatsApp links, website, and company LinkedIn from the official packaged assets:
- signatures/dist/thunderbird/info-com.html
- signatures/dist/thunderbird/info-it.html
"""

import os
from pathlib import Path
from typing import Dict, Optional, Literal

# Load official HTML signatures from packaged brand assets
_BRAND_ASSETS_DIR = Path(__file__).resolve().parent / "brand_assets"
_INFO_COM_HTML_PATH = _BRAND_ASSETS_DIR / "signatures" / "dist" / "thunderbird" / "info-com.html"
_INFO_IT_HTML_PATH = _BRAND_ASSETS_DIR / "signatures" / "dist" / "thunderbird" / "info-it.html"

_OFFICIAL_INFO_COM_HTML = _INFO_COM_HTML_PATH.read_text(encoding="utf-8").strip() if _INFO_COM_HTML_PATH.exists() else ""
_OFFICIAL_INFO_IT_HTML = _INFO_IT_HTML_PATH.read_text(encoding="utf-8").strip() if _INFO_IT_HTML_PATH.exists() else ""

# Corresponding faithful plain-text representations reflecting official contact data
_OFFICIAL_INFO_COM_PLAIN = (
    "--\n"
    "pyBIM\n"
    "Engineering & Digital Automation\n"
    "Email: info@pybim.com\n"
    "Mobile: +39 352 288 7878\n"
    "WhatsApp: https://wa.me/393522887878\n"
    "Website: https://www.pybim.com\n"
    "Company LinkedIn: https://www.linkedin.com/company/pybim"
)

_OFFICIAL_INFO_IT_PLAIN = (
    "--\n"
    "pyBIM\n"
    "Ingegneria & Automazione Digitale\n"
    "Email: info@pybim.it\n"
    "Cellulare: +39 352 288 7878\n"
    "WhatsApp: https://wa.me/393522887878\n"
    "Sito web: https://www.pybim.it\n"
    "LinkedIn aziendale: https://www.linkedin.com/company/pybim"
)

SIGNATURES_REGISTRY: Dict[str, Dict[str, str]] = {
    "info@pybim.com": {
        "id": "info-com",
        "name": "pyBIM Global (info@pybim.com)",
        "language": "en",
        "plain": _OFFICIAL_INFO_COM_PLAIN,
        "html": _OFFICIAL_INFO_COM_HTML,
    },
    "info@pybim.it": {
        "id": "info-it",
        "name": "pyBIM Italia (info@pybim.it)",
        "language": "it",
        "plain": _OFFICIAL_INFO_IT_PLAIN,
        "html": _OFFICIAL_INFO_IT_HTML,
    },
}

_VERIFIED_SIGNATURES = SIGNATURES_REGISTRY


def get_default_signature(sender_or_account: str) -> Dict[str, str]:
    """
    Retrieve official pyBIM email signature for an authorized sender account.
    Accepts either account_id ('acc_info_com') or email ('info@pybim.com').
    Returns dict with 'plain' and 'html' keys.
    """
    clean = (sender_or_account or "").strip().lower()
    if clean in ("acc_info_com", "info@pybim.com", "info-com"):
        return dict(SIGNATURES_REGISTRY["info@pybim.com"])
    if clean in ("acc_info_it", "info@pybim.it", "info-it"):
        return dict(SIGNATURES_REGISTRY["info@pybim.it"])
    return {
        "plain": "",
        "html": "",
    }


def get_signature_html_with_local_preview(html_content: str) -> str:
    """
    For safe in-browser composer preview without external CDN requests:
    Rewrites the remote logo URL to the local static mount /static/brand/logo_white_transparent.png.
    """
    if not html_content:
        return ""
    return html_content.replace(
        "https://www.pybim.com/logo_white_transparent.png",
        "/static/brand/logo_white_transparent.png",
    )


def strip_signature(plain_text: str) -> str:
    """
    Strips appended standard official pyBIM signature block from plain text email body.
    Isolates pure user message content to prevent duplicate signature accumulation.
    """
    if not plain_text:
        return ""

    text = plain_text
    # Known signatures to strip
    signatures = [
        _OFFICIAL_INFO_COM_PLAIN,
        _OFFICIAL_INFO_IT_PLAIN,
        # Also handle potential variations without exact line returns or spaces
        "pyBIM\nEngineering & Digital Automation\nEmail: info@pybim.com",
        "pyBIM\nIngegneria & Automazione Digitale\nEmail: info@pybim.it",
    ]

    for sig in signatures:
        if sig in text:
            # Split and keep the part before the signature
            text = text.split(sig)[0]

    # Clean standard '--' signature delimiter if it was left at the end
    text = text.rstrip()
    if text.endswith("--"):
        text = text[:-2].rstrip()

    return text


def strip_signature_html(html_text: str) -> str:
    """
    Strips appended official pyBIM HTML signature block if present.
    """
    if not html_text:
        return ""

    text = html_text
    # Check for table containing pyBIM logo or signature markers
    # In compose view, signature is appended at bottom with table
    sig_markers = [
        '<table cellpadding="0" cellspacing="0" border="0" width="500"',
        '<table cellpadding="0" cellspacing="0" border="0" width="500" style="width: 500px;',
        'Engineering & Digital Automation',
        'Ingegneria & Automazione Digitale',
    ]

    for marker in sig_markers:
        idx = text.find(marker)
        if idx != -1:
            # Find beginning of <table before this marker or at this marker
            table_start = text.rfind("<table", 0, idx + 1)
            if table_start != -1:
                text = text[:table_start]
            else:
                text = text[:idx]

    # Clean trailing breaks / empty paragraphs
    text = text.rstrip()
    for empty_tag in ["<br>", "<br/>", "<br />", "<p><br></p>", "<p></p>"]:
        while text.endswith(empty_tag):
            text = text[:-len(empty_tag)].rstrip()

    return text
