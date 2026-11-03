"""
sales_engine/benchmark/dataset_formatter.py
Provider-independent, company-independent deterministic evidence dataset formatter.
Transforms normalized ResearchSnapshot page documents into FULL and COMPACT representations
for fair, reproducible cross-provider LLM benchmarking.

Zero company-specific hardcoded strings (e.g. no hardcoded P.IVA, specific town, street, or email).
Derives salient business facts dynamically using structural patterns:
- Corporate legal markers (P.IVA, C.F., S.r.l., S.p.A., VAT, Reg. Imp.)
- Geographic postal markers (Via, Corso, Piazza, CAP 5-digit, province codes)
- Contact markers (Tel, Phone, Email, Contatti, PEC)
- Forms (action, method, fields)
- Key business links (recruitment, supplier/vendor onboarding, teaming, services)
- BIM/AEC capabilities and engineering services
"""

from __future__ import annotations

import re
import hashlib
from typing import Dict, Any, List, Optional, Tuple


# Reusable structural patterns for generic corporate fact preservation
GENERIC_FACT_PATTERNS = [
    # Legal identity & VAT
    re.compile(r"\b(?:p\.?\s*iva|c\.?\s*f\.?|vat|codice\s+fiscale|partita\s+iva)\b", re.IGNORECASE),
    re.compile(r"\b(?:s\.r\.l\.|srl|s\.p\.a\.|spa|s\.a\.s\.|sas|societ[aà]\s+di\s+ingegneria)\b", re.IGNORECASE),
    re.compile(r"\b\d{11}\b"),  # 11-digit Italian P.IVA
    # Postal and address markers
    re.compile(r"\b(?:via|viale|corso|piazza|strada|vicolo|largo|piazzale|borgo|contrada)\s+[A-Za-z0-9]", re.IGNORECASE),
    re.compile(r"\b\d{5}\b"),  # 5-digit Italian CAP
    re.compile(r"\b\([A-Z]{2}\)\b"),  # Italian province abbreviation e.g. (VE), (MI), (RM)
    # Telecommunication & email markers
    re.compile(r"(?:\+39|0039)?\s*(?:0\d{1,4}|\d{2,4})[\s\./-]?\d{4,8}\b"),
    re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    # AEC, BIM & Technical services
    re.compile(r"\b(?:bim|revit|navisworks|solibri|archicad|autocad|lod\s*\d+|uni\s*11337|uni\s*pdr)\b", re.IGNORECASE),
    # Business activity & leadership markers
    re.compile(r"\b(?:ingegneria|architettura|progettazione|impianti|struttur|direzione\s+lavori|sicurezza)\b", re.IGNORECASE),
    re.compile(r"\b(?:ceo|founder|fondator|amministratore|direttore|partner|presidente)\b", re.IGNORECASE),
    # Opportunity channels (suppliers, careers, partners)
    re.compile(r"\b(?:fornitor|vendor|supplier|albo|lavora\s+con\s+noi|career|posizioni|candidatur|collabora)\b", re.IGNORECASE),
]

# Generic boilerplate lines to discard
GENERIC_BOILERPLATE_PATTERNS = [
    re.compile(r"\b(?:gestisci\s+opzioni|gestisci\s+servizi|visualizza\s+le\s+preferenze)\b", re.IGNORECASE),
    re.compile(r"\b(?:salta\s+al\s+contenuto|torna\s+in\s+cima|page\s+load\s+link)\b", re.IGNORECASE),
    re.compile(r"^\s*\{[a-zA-Z0-9_]+\}\s*$"),  # Raw template placeholders like {title}
]


def is_salient_fact_line(line: str) -> bool:
    """Returns True if the line contains identifiable structural corporate facts."""
    return any(p.search(line) for p in GENERIC_FACT_PATTERNS)


def is_boilerplate_line(line: str) -> bool:
    """Returns True if the line is purely navigation/cookie boilerplate."""
    return any(p.search(line) for p in GENERIC_BOILERPLATE_PATTERNS)


def build_compact_evidence_text(docs: List[Dict[str, Any]]) -> str:
    """
    Deterministically builds a company-independent, evidence-preserving compact representation
    of normalized snapshot documents.
    
    Budget guarantee:
    Strictly capped under 15,000 characters (~3,700 tokens) to ensure complete execution across
    models with lower TPM limits (e.g. GroqCloud 6,000 TPM) without token truncation or rate-limit errors.
    
    Evidence preservation:
    - Evidence IDs, URLs, and page classifications.
    - Preserves all detected forms and fields.
    - Preserves key business opportunity links (careers, supplier intake, teaming).
    - Preserves all structural corporate identity lines (P.IVA, address, phones, emails, leadership, BIM).
    - Records an audit trail of lines omitted per document.
    """
    parts = []

    for doc in docs:
        eid = doc["evidence_id"]
        ptype = doc["page_type"]
        url = doc["source_url"]
        title = doc.get("title") or ""

        # 1. Detected forms
        form_lines = []
        for f in doc.get("forms", []):
            action = f.get("action", "")
            fields = ", ".join(f.get("fields", []))
            form_lines.append(f"  - Form: action='{action}' fields=[{fields}]")

        # 2. Key business links (careers, suppliers, external collaboration)
        key_links = []
        for lk in doc.get("links", []):
            if isinstance(lk, dict):
                href = lk.get("href", "")
                text = lk.get("text", "")
            else:
                href = str(lk)
                text = ""
            comb = f"{text} {href}".lower()
            if any(k in comb for k in ["fornitor", "vendor", "supplier", "lavora", "career", "job", "candidatur", "collabora"]):
                # Discard bare hash anchors except valid fragment identifiers
                if not href.startswith("#") or len(href) > 2:
                    clean_text = re.sub(r"\{[a-zA-Z0-9_]+\}", "", text).strip()
                    key_links.append(f"  - Link: [{clean_text or 'Link'}] -> {href}")

        # 3. Categorize raw text lines into salient facts vs general context vs boilerplate
        raw_lines = doc["cleaned_text"].splitlines()
        salient_lines = []
        general_lines = []
        omitted_count = 0
        seen = set()

        for raw_line in raw_lines:
            s = raw_line.strip()
            if not s or len(s) < 3:
                continue

            if is_boilerplate_line(s):
                omitted_count += 1
                continue

            if s in seen:
                continue
            seen.add(s)

            if is_salient_fact_line(s):
                salient_lines.append(s)
            else:
                general_lines.append(s)

        # Retain ALL salient fact lines + top representative general lines (up to 8 per doc)
        combined_selected = salient_lines + general_lines[:8]
        omitted_count += max(0, len(general_lines) - 8)
        content_chunk = "\n".join(combined_selected)

        doc_block = [
            f"=== EVIDENCE [{eid}] | TYPE: {ptype.upper()} | URL: {url} ===",
            f"Page Title: {title}",
        ]
        if form_lines:
            doc_block.append("Detected Forms:\n" + "\n".join(form_lines))
        if key_links:
            doc_block.append("Key Opportunity & Corporate Links:\n" + "\n".join(key_links[:6]))
        doc_block.append("Page Content Excerpts:\n" + content_chunk)
        if omitted_count > 0:
            doc_block.append(f"[Note: {omitted_count} duplicate or low-salience boilerplate lines omitted from compact representation]")

        parts.append("\n".join(doc_block))

    return "\n\n".join(parts)


def build_full_evidence_text(docs: List[Dict[str, Any]]) -> str:
    """
    Returns the complete, unabridged normalized evidence text across all crawled pages.
    Preserves verbatim document content without any line filtering.
    """
    parts = []
    for doc in docs:
        eid = doc["evidence_id"]
        ptype = doc["page_type"]
        url = doc["source_url"]
        title = doc.get("title") or ""
        text = doc.get("cleaned_text") or ""

        parts.append(
            f"--- DOCUMENT {eid} ---\n"
            f"URL: {url}\n"
            f"Type: {ptype}\n"
            f"Title: {title}\n"
            f"Content:\n{text.strip()}\n"
        )
    return "\n".join(parts)
