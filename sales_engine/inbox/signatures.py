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
