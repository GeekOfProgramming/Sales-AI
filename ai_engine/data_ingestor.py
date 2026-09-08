"""Scrapling-based Data Ingestion Engine for Revit API & BIM Knowledge Base.
Fetches, cleans, converts online documentation into structured Markdown,
and indexes the resulting knowledge chunks directly into the ChromaDB vector store.
"""

import re
import os
from pathlib import Path
from typing import Optional, Dict, Any, List

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

    def save_markdown_rule(self, rule_slug: str, markdown_content: str) -> Path:
        """Save clean structured Markdown into data/rules directory."""
        # Sanitize filename slug
        safe_name = re.sub(r"[^\w\-_\.]", "_", rule_slug).lower()
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
        # Derive slug if not provided
        effective_slug = slug or rule_slug
        if not effective_slug:
            clean_url = url.split("?")[0].rstrip("/")
            effective_slug = clean_url.split("/")[-1] or "web_documentation"

        raw_html = self.fetch_url(url)
        parsed = self.clean_and_convert(raw_html, title=title, main_selector=main_selector)
        
        file_path = self.save_markdown_rule(effective_slug, parsed["markdown"])


        indexed_chunks = 0
        if reindex:
            indexed_chunks = self.retriever.index_file(file_path)

        return {
            "rule_slug": rule_slug,
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
