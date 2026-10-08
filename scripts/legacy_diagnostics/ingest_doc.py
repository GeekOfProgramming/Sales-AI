"""CLI Tool: Ingest Online Webpages or Markdown into pyBIM-LLM RAG Vector Store.
Usage:
    python ingest_doc.py https://example.com/revit-docs --slug revit_api_walls
    python ingest_doc.py path/to/local.md --slug company_standards
"""

import sys
import io
import argparse
from pathlib import Path

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ai_engine.data_ingestor import BIMDataIngestor


def main():
    parser = argparse.ArgumentParser(description="Ingest documentation into pyBIM-LLM RAG Vector Store using Scrapling.")
    parser.add_argument("source", help="URL (http/https) or local file path to ingest")
    parser.add_argument("--slug", help="Rule filename slug (e.g. revit_api_clash_detection)", required=True)
    parser.add_argument("--title", help="Optional title for the document", default=None)
    parser.add_argument("--selector", help="CSS selector for main content area (e.g. article, main, #content)", default=None)

    args = parser.parse_args()

    print("=" * 65)
    print("📥 pyBIM-LLM Knowledge Ingestion Engine (Powered by Scrapling)")
    print("=" * 65)

    ingestor = BIMDataIngestor()

    source = args.source.strip()
    slug = args.slug.strip()

    if source.startswith("http://") or source.startswith("https://"):
        print(f"\n🌐 Fetching online page via Scrapling: {source}")
        res = ingestor.ingest_url(url=source, rule_slug=slug, title=args.title, main_selector=args.selector)
    else:
        local_path = Path(source)
        if not local_path.exists():
            print(f"❌ Error: Local file not found at: {local_path}")
            sys.exit(1)
        print(f"\n📄 Reading local file: {local_path}")
        content = local_path.read_text(encoding="utf-8")
        title = args.title or local_path.stem.replace("_", " ").title()
        res = ingestor.ingest_raw_documentation(rule_slug=slug, title=title, markdown_text=content)

    print("\n✅ INGESTION SUCCESSFUL!")
    print(f"  • Rule Title:       {res['title']}")
    print(f"  • Saved File:       {res['file_path']}")
    print(f"  • Indexed Chunks:   {res['indexed_chunks']}")
    print(f"  • Total DB Chunks:  {res['total_rules_in_db']}")
    print("\n🎉 The local AI assistant can now use this knowledge immediately!")


if __name__ == "__main__":
    main()
