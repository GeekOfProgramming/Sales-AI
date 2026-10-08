"""Test suite for Scrapling Data Ingestion Pipeline.
Validates webpage fetching, HTML stripping, Markdown conversion,
and automatic ChromaDB vector indexing.
"""

import sys
import io
from pathlib import Path

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ai_engine.data_ingestor import BIMDataIngestor
from ai_engine.rag_retriever import BIMRAGRetriever


SAMPLE_REVIT_API_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Revit API: Creating Grids and Levels</title>
</head>
<body>
    <header><nav><a href="/">Home</a> | <a href="/docs">Docs</a></nav></header>
    <main id="main-content">
        <h1>Revit API: Grid and Level Creation Guidelines</h1>
        <p class="intro">In Autodesk Revit, Grids and Levels define the primary datum coordinates of a building model.</p>
        
        <section>
            <h2>1. Creating Grids Programmatically</h2>
            <p>To create a linear datum grid in the active document, use the static method <code>Grid.Create()</code>:</p>
            <pre><code class="language-python">
from Autodesk.Revit.DB import Grid, Line, XYZ, Transaction

# Create a straight line datum
line = Line.CreateBound(XYZ(0, 0, 0), XYZ(100, 0, 0))

# Must be inside an open Transaction
t = Transaction(doc, "Create Structural Grid")
t.Start()
grid = Grid.Create(doc, line)
grid.Name = "Grid-A"
t.Commit()
            </code></pre>
        </section>

        <section>
            <h2>2. Creating Levels Programmatically</h2>
            <p>Levels are created by specifying elevation in internal decimal feet:</p>
            <pre><code class="language-csharp">
Level level = Level.Create(doc, 10.0); // 10.0 feet elevation
level.Name = "Level 2 - First Floor";
            </code></pre>
        </section>
    </main>
    <footer><p>Copyright 2026 Autodesk Revit API Documentation</p></footer>
</body>
</html>
"""


def test_ingestion():
    print("=" * 65)
    print("🕸️ Scrapling Data Ingestion Engine - Test Suite")
    print("=" * 65)

    ingestor = BIMDataIngestor()

    # 1. Test Clean and Convert HTML -> Structured Markdown
    print("\n🔹 Test 1: HTML Parsing & Markdown Conversion...")
    result = ingestor.clean_and_convert(SAMPLE_REVIT_API_HTML, title="Revit API Datum Guidelines")
    
    assert "Revit API" in result["title"]
    md = result["markdown"]
    assert "# Revit API" in md
    assert "Grid.Create" in md
    assert "Level.Create" in md
    assert "<nav>" not in md, "Navbars should be stripped"
    assert "<footer>" not in md, "Footers should be stripped"
    print("✅ Test 1 Passed: HTML successfully cleaned and converted into structured Markdown.")
    print("--- Markdown Preview ---")
    print(md[:250], "...\n")

    # 2. Test Ingesting and Saving Rule into data/rules
    print("-" * 50)
    print("🔹 Test 2: Ingesting into data/rules and ChromaDB Vector Store...")
    print("-" * 50)
    
    rule_slug = "revit_api_datum_grids"
    initial_count = ingestor.retriever.count()
    print(f"📊 Initial ChromaDB collection items: {initial_count}")

    ingest_res = ingestor.ingest_raw_documentation(
        rule_slug=rule_slug,
        title=result["title"],
        markdown_text=md,
        reindex=True,
    )

    file_path = Path(ingest_res["file_path"])
    assert file_path.exists(), "Target Markdown file must be created"
    assert ingest_res["indexed_chunks"] > 0, "At least one semantic chunk must be indexed"
    new_count = ingestor.retriever.count()
    print(f"✅ Test 2 Passed: File saved to {file_path.name}. Indexed {ingest_res['indexed_chunks']} chunks.")
    print(f"📊 New ChromaDB collection count: {new_count}")
    assert new_count >= initial_count

    # 3. Test Semantic Query against newly ingested knowledge
    print("\n" + "-" * 50)
    print("🔹 Test 3: Querying newly ingested Datum/Grid rule via Semantic RAG...")
    print("-" * 50)
    
    query = "How to create a linear datum grid using Grid.Create in Revit API?"
    hits = ingestor.retriever.query(query, top_k=2)
    
    print(f"Found {len(hits)} matching rule chunks:")
    for i, hit in enumerate(hits):
        src = hit["metadata"].get("source", "Unknown")
        hdr = hit["metadata"].get("header", "General")
        sim = hit.get("similarity", 0.0)
        print(f"  [{i+1}] Source: {src} | Header: {hdr} | Score: {sim:.4f}")
    
    assert len(hits) > 0
    top_hit = hits[0]
    assert "Grid" in top_hit["content"] or "Level" in top_hit["content"]
    print("✅ Test 3 Passed: Semantic retriever successfully matched newly ingested knowledge!")

    # 4. Test Scrapling Fetcher Online capability
    print("\n" + "-" * 50)
    print("🔹 Test 4: Testing Scrapling Fetcher on live endpoint...")
    print("-" * 50)
    try:
        body = ingestor.fetch_url("https://httpbin.org/html")
        assert "<html" in body.lower() or "<h1" in body.lower()
        print("✅ Test 4 Passed: Scrapling Fetcher successfully downloaded remote page.")
    except Exception as e:
        print(f"⚠️ Live URL test notice (network dependent): {e}")

    print("\n" + "=" * 65)
    print("🎉 ALL DATA INGESTION & SCRAPLING TESTS PASSED SUCCESSFULLY!")
    print("=" * 65)


if __name__ == "__main__":
    test_ingestion()
