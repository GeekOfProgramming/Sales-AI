import re
from typing import Optional
from bs4 import BeautifulSoup

def sanitize_email_html(raw_html: Optional[str]) -> str:
    """
    Sanitize untrusted email HTML for secure read-only display.
    - Strips executable tags (<script>, <style>, <iframe>, <object>, <embed>, <form>, <link>, <meta>)
    - Strips all JavaScript event handlers (e.g. onload, onclick, onerror)
    - Strips dangerous javascript: or data: URIs in links
    - Defangs remote tracking images (<img src="..."> -> data-src with placeholder alt)
    - Ensures target='_blank' and rel='noopener noreferrer' on safe links
    """
    if not raw_html:
        return ""

    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Remove dangerous active elements
    for dangerous_tag in soup(["script", "style", "iframe", "object", "embed", "form", "link", "meta", "base", "applet"]):
        dangerous_tag.decompose()

    # 2. Iterate all tags to sanitize attributes
    for tag in soup.find_all(True):
        attrs = dict(tag.attrs)
        for attr, val in attrs.items():
            # Remove all JS event handlers (e.g. onclick, onmouseover)
            if attr.lower().startswith("on"):
                del tag.attrs[attr]
            # Defang javascript: links
            elif attr.lower() in ("href", "src") and isinstance(val, str):
                cleaned_val = val.strip().lower()
                if cleaned_val.startswith("javascript:") or cleaned_val.startswith("data:text/html"):
                    del tag.attrs[attr]

        # 3. Defang remote images to prevent email read receipts / tracking pixels
        if tag.name == "img":
            if "src" in tag.attrs:
                src_val = tag.attrs["src"]
                # If remote http/https image, block automatic loading
                if isinstance(src_val, str) and src_val.strip().lower().startswith(("http://", "https://", "//")):
                    tag.attrs["data-src"] = src_val
                    del tag.attrs["src"]
                    tag.attrs["alt"] = "[Remote tracking image blocked]"
                    tag.attrs["class"] = (tag.attrs.get("class", []) or []) + ["blocked-remote-image"]

        # 4. Safe link targeting
        if tag.name == "a":
            tag.attrs["target"] = "_blank"
            tag.attrs["rel"] = "noopener noreferrer"

    return str(soup)
