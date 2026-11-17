"""
sales_engine/inbox/signatures.py
MAIL-COMPOSE-01A: Safe Extensibility Point for pyBIM Email Signatures.

Provides verified, per-sender pyBIM email signatures without inventing unverified
business contact details or hardcoding speculative marketing claims.
"""

from typing import Dict, Optional


# Verified pyBIM sender signatures based on authoritative domain and verified team identities
SIGNATURES_REGISTRY: Dict[str, Dict[str, str]] = {
    "info@pybim.com": {
        "plain": (
            "--\n"
            "pyBIM Team\n"
            "info@pybim.com\n"
            "https://pybim.com"
        ),
        "html": (
            "<p>--<br>"
            "<strong>pyBIM Team</strong><br>"
            "<a href=\"mailto:info@pybim.com\">info@pybim.com</a><br>"
            "<a href=\"https://pybim.com\" target=\"_blank\" rel=\"noopener noreferrer\">pybim.com</a>"
            "</p>"
        ),
    },
    "info@pybim.it": {
        "plain": (
            "--\n"
            "Team pyBIM Italia\n"
            "info@pybim.it\n"
            "https://pybim.it"
        ),
        "html": (
            "<p>--<br>"
            "<strong>Team pyBIM Italia</strong><br>"
            "<a href=\"mailto:info@pybim.it\">info@pybim.it</a><br>"
            "<a href=\"https://pybim.it\" target=\"_blank\" rel=\"noopener noreferrer\">pybim.it</a>"
            "</p>"
        ),
    },
}
_VERIFIED_SIGNATURES = SIGNATURES_REGISTRY


def get_default_signature(sender_or_account: str) -> Dict[str, str]:
    """
    Retrieve verified pyBIM email signature for an authorized sender account.
    Accepts either account_id ('acc_info_com') or email ('info@pybim.com').
    Returns dict with 'plain' and 'html' keys.
    """
    clean = (sender_or_account or "").strip().lower()
    if clean in ("acc_info_com", "info@pybim.com"):
        return dict(SIGNATURES_REGISTRY["info@pybim.com"])
    if clean in ("acc_info_it", "info@pybim.it"):
        return dict(SIGNATURES_REGISTRY["info@pybim.it"])
    return {
        "plain": "",
        "html": "",
    }
