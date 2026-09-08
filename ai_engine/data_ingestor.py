"""Scrapling-based Data Ingestion Engine for Revit API & BIM Knowledge Base.
Fetches, cleans, converts online documentation into structured Markdown,
and indexes the resulting knowledge chunks directly into the ChromaDB vector store.
"""

import re
import os
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from scrapling import Fetcher, Selector
from markdownify import markdownify

from ai_engine.rag_retriever import BIMRAGRetriever


class BIMDataIngestor:
    """Automated web ingestion and structuring pipeline for BIM & Revit API documentation."""

    def __init__(
        self,
        rules_dir: Optional[str] = None,
        persist_dir: str = "chroma_db",
        retriever: Optional[BIMRAGRetriever] = None,
    ):
        project_root = Path(__file__).parent.parent
        self.rules_dir = Path(rules_dir) if rules_dir else project_root / "data" / "rules"
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        
        self.persist_dir = str(project_root / persist_dir) if not Path(persist_dir).is_absolute() else persist_dir
        self.retriever = retriever or BIMRAGRetriever(persist_dir=self.persist_dir)
        self.fetcher = Fetcher()

    def fetch_url(self, url: str) -> str:
        """Fetch online webpage content using Scrapling anti-detection fetcher."""
        response = self.fetcher.get(url)
        if response.status != 200:
            raise RuntimeError(f"Failed to fetch {url}. HTTP Status: {response.status}")
        return response.body.decode("utf-8", errors="replace")

    def clean_and_convert(
        self,
        html_content: str,
        title: Optional[str] = None,
        main_selector: Optional[str] = None,
    ) -> Dict[str, str]:
        """Parse HTML using Scrapling Selector, remove noise, and convert to clean Markdown."""
        page = Selector(html_content)

        # 1. Extract Page Title if not provided
        if not title:
            h1_el = page.css("h1::text").get()
            title_el = page.css("title::text").get()
            title = (h1_el or title_el or "BIM Documentation").strip()

        # 2. Locate main content container using candidate CSS selectors
        content_html = ""
        candidate_selectors = [
            main_selector,
            "article",
            "main",
            "#content",
            ".documentation",
            ".topic-content",
            ".markdown-body",
            "#main-content",
            "body",
        ]

        for sel in candidate_selectors:
            if not sel:
                continue
            matched = page.css(sel)
            if matched:
                content_html = matched.get()
                break

        if not content_html:
            content_html = html_content

        # 3. Strip noisy elements (scripts, styles, navbars, ads)
        cleaned_html = re.sub(r"<(script|style|nav|footer|header|aside|iframe)[^>]*>.*?</\1>", "", content_html, flags=re.DOTALL | re.IGNORECASE)

        # 4. Convert to structured Markdown
        md_text = markdownify(
            cleaned_html,
            heading_style="ATX",
            code_language="python",
            strip=["a", "img"],
        )

        # 5. Clean up excessive whitespace
        md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip()

        # Ensure top-level heading exists
        if not md_text.startswith("#"):
            md_text = f"# {title}\n\n{md_text}"

        return {
            "title": title,
            "markdown": md_text,
        }

    @staticmethod
    def is_guid_or_hash(text: str) -> bool:
        """Detect if a string is a raw GUID or hexadecimal hash."""
        cleaned = re.sub(r"\.html?$", "", text.strip().lower())
        guid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        hex_pattern = r"^[0-9a-f]{16,}$"
        return bool(re.match(guid_pattern, cleaned) or re.match(hex_pattern, cleaned))

    @classmethod
    def generate_smart_slug(
        cls,
        url: str,
        title: Optional[str] = None,
        user_slug: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        Derives an optimal, human-readable, and standard snake_case slug.
        Determines domain prefix (revit_api, pyrevit, dynamo, iso),
        cleans title or URL segments, and provides feedback if user's slug was unstandardized.
        Returns: (effective_slug, slug_feedback_message)
        """
        url_lower = url.lower()
        if "pyrevit" in url_lower:
            prefix = "pyrevit"
        elif "revitapidocs.com" in url_lower or "revit" in url_lower:
            prefix = "revit_api"
        elif "dynamo" in url_lower:
            prefix = "dynamo"
        elif "iso" in url_lower or "19650" in url_lower:
            prefix = "iso"
        else:
            prefix = "bim"

        title_clean = ""
        if title:
            # Clean common generic keywords to focus on actual class/method/subject
            t = re.sub(r"\b(revit|api|docs|pyrevit|autodesk|documentation)\b", "", title, flags=re.IGNORECASE)
            t = re.sub(r"[^a-zA-Z0-9\s_]", " ", t)
            words = [w.lower() for w in t.split() if len(w) > 1]
            title_clean = "_".join(words)

        if not title_clean:
            # Fallback parsing path segments from URL
            parts = [p for p in url.split("/") if p and not cls.is_guid_or_hash(p) and not p.startswith("202")]
            candidate = parts[-1] if parts else "documentation"
            candidate = re.sub(r"\.html?$", "", candidate)
            title_clean = re.sub(r"[^a-zA-Z0-9_]", "_", candidate).strip("_")

        suggested_slug = f"{prefix}_{title_clean}".strip("_")
        suggested_slug = re.sub(r"_+", "_", suggested_slug)

        # 1. If user provided no slug
        if not user_slug or not user_slug.strip():
            return suggested_slug, None

        # 2. Analyze user-provided slug
        cleaned_user = re.sub(r"\.html?$", "", user_slug.strip().lower())
        cleaned_user = re.sub(r"[^a-zA-Z0-9_]", "_", cleaned_user)
        cleaned_user = re.sub(r"_+", "_", cleaned_user).strip("_")

        # Check if user slug was unstandardized (raw GUID, too short, or meaningless keyword)
        if cls.is_guid_or_hash(user_slug) or len(cleaned_user) < 3 or cleaned_user in ["test", "doc", "file", "script", "revit"]:
            feedback = f"Provided slug '{user_slug}' was unstandardized/generic. Optimized to standard identifier '{suggested_slug}'."
            return suggested_slug, feedback

        # If user provided a valid slug with spaces or extension that got normalized
        if cleaned_user != user_slug.strip():
            feedback = f"Normalized slug from '{user_slug.strip()}' to snake_case '{cleaned_user}'."
            return cleaned_user, feedback

        return cleaned_user, None

    def save_markdown_rule(self, rule_slug: str, markdown_content: str) -> Path:
        """Save clean structured Markdown into data/rules directory."""
        # Sanitize filename slug: remove HTML extensions and ensure valid filename
        safe_name = re.sub(r"[^\w\-_\.]", "_", rule_slug).lower()
        safe_name = re.sub(r"\.html?$", "", safe_name)
        safe_name = re.sub(r"_+", "_", safe_name).strip("_")
        if not safe_name.endswith(".md"):
            safe_name += ".md"

        target_file = self.rules_dir / safe_name
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return target_file

    def ingest_url(
        self,
        url: str,
        rule_slug: Optional[str] = None,
        slug: Optional[str] = None,
        title: Optional[str] = None,
        main_selector: Optional[str] = None,
        reindex: bool = True,
    ) -> Dict[str, Any]:
        """End-to-end ingestion: Fetch online URL via Scrapling, convert to Markdown, save, and index."""
        raw_html = self.fetch_url(url)
        parsed = self.clean_and_convert(raw_html, title=title, main_selector=main_selector)

        # Derive smart standardized slug and evaluate user input
        user_provided_slug = slug or rule_slug
        effective_slug, slug_feedback = self.generate_smart_slug(
            url=url,
            title=parsed["title"],
            user_slug=user_provided_slug,
        )

        file_path = self.save_markdown_rule(effective_slug, parsed["markdown"])

        indexed_chunks = 0
        if reindex:
            indexed_chunks = self.retriever.index_file(file_path)

        return {
            "rule_slug": effective_slug,
            "effective_slug": effective_slug,
            "user_provided_slug": user_provided_slug,
            "slug_feedback": slug_feedback,
            "title": parsed["title"],
            "file_path": str(file_path),
            "markdown_preview": parsed["markdown"][:300] + "...",
            "indexed_chunks": indexed_chunks,
            "total_rules_in_db": self.retriever.count(),
        }

    def ingest_raw_documentation(
        self,
        rule_slug: str,
        title: str,
        markdown_text: str,
        reindex: bool = True,
    ) -> Dict[str, Any]:
        """Ingest pre-formatted documentation or manual Revit API notes directly."""
        formatted_content = markdown_text.strip()
        if not formatted_content.startswith("#"):
            formatted_content = f"# {title}\n\n{formatted_content}"

        file_path = self.save_markdown_rule(rule_slug, formatted_content)

        indexed_chunks = 0
        if reindex:
            indexed_chunks = self.retriever.index_file(file_path)

        return {
            "rule_slug": rule_slug,
            "title": title,
            "file_path": str(file_path),
            "indexed_chunks": indexed_chunks,
            "total_rules_in_db": self.retriever.count(),
        }
