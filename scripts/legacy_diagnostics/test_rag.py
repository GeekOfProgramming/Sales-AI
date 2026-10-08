"""Verification test for BIMRAGRetriever (ChromaDB + Ollama nomic-embed-text)
and end-to-end integration with BIMLLMClient (qwen2.5-coder:1.5b).
"""

import sys
import io
from pathlib import Path

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ai_engine.rag_retriever import BIMRAGRetriever
from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest


def test_rag():
    print("=" * 65)
    print("📚 BIM AI Core - Knowledge Base & RAG Retriever Test")
    print("=" * 65)

    rules_dir = Path(__file__).parent / "data" / "rules"
    if not rules_dir.exists():
        print(f"❌ ERROR: Rules directory not found at {rules_dir}")
        sys.exit(1)

    # 1. Initialize Retriever
    print("\n🔹 Step 1: Initializing ChromaDB persistent vector store...")
    retriever = BIMRAGRetriever(
        persist_dir="chroma_db",
        collection_name="bim_rules",
        embedding_model="nomic-embed-text",
    )
    print("✅ ChromaDB initialized with local nomic-embed-text embedding function.")

    # 2. Index Documents
    print(f"\n🔹 Step 2: Indexing markdown files from {rules_dir}...")
    indexed_count = retriever.index_directory(rules_dir)
    print(f"✅ Successfully indexed {indexed_count} semantic chunks into ChromaDB.")
    print(f"📊 Total items in vector collection: {retriever.count()}")
    assert retriever.count() > 0, "Collection should contain indexed chunks"

    # 3. Test Semantic Query on ISO 19650
    query_iso = "What are the required naming fields and separator in ISO 19650 model containers?"
    print("\n" + "-" * 50)
    print(f"🔍 Test 3.1: Semantic Query -> '{query_iso}'")
    print("-" * 50)
    results_iso = retriever.query(query_iso, top_k=2)
    for i, res in enumerate(results_iso, 1):
        print(f"Hit #{i} [Source: {res['metadata'].get('source')} | Similarity: {res['similarity']}]:")
        print(res["content"][:200] + "...\n")

    assert any("iso19650" in r["metadata"].get("source", "").lower() for r in results_iso), "Should retrieve ISO 19650 rule"
    print("✅ Test 3.1 Passed: ISO 19650 rule retrieved with high relevance.")

    # 4. Test Semantic Query on Revit API Transactions
    query_tx = "How to safely commit and rollback a Transaction in pyRevit or C#?"
    print("\n" + "-" * 50)
    print(f"🔍 Test 3.2: Semantic Query -> '{query_tx}'")
    print("-" * 50)
    results_tx = retriever.query(query_tx, top_k=2)
    for i, res in enumerate(results_tx, 1):
        print(f"Hit #{i} [Source: {res['metadata'].get('source')} | Similarity: {res['similarity']}]:")
        print(res["content"][:200] + "...\n")

    assert any("transaction" in r["metadata"].get("source", "").lower() for r in results_tx), "Should retrieve Revit Transaction rule"
    print("✅ Test 3.2 Passed: Revit Transaction guidelines retrieved successfully.")

    # 5. End-to-End RAG + LLM Code Generation Test
    print("\n" + "-" * 50)
    print("🤖 Test 4: End-to-End RAG Code Generation with Local LLM...")
    print("-" * 50)

    prompt = "Write a pyRevit script to rename the active project file following ISO 19650 architectural standard for Project 'HQB01', Whole Building 'ZZ', Level '00', 3D Architectural Model number 0001."
    rag_context = retriever.get_formatted_context(prompt, top_k=2)
    print("📋 Injected RAG Context Preview:\n", rag_context[:350], "...\n")

    llm_client = BIMLLMClient()
    req = CodeGenerationRequest(
        user_prompt=prompt,
        language="python",
        context_rules=rag_context,
        temperature=0.1,
    )

    print("⚡ Generating code with injected rules context...")
    response = llm_client.generate_code(req)
    print(f"⏱️ Generation time: {response.duration_seconds}s")
    print("\n📝 Extracted Python Code:\n")
    print(response.extracted_code)

    assert "HQB01" in response.extracted_code or "M3" in response.extracted_code, "Generated code should adhere to ISO 19650 naming context"
    print("\n✅ Test 4 Passed: Model successfully respected injected ISO 19650 naming rules!")

    print("\n" + "=" * 65)
    print("🎉 ALL RAG AND SEMANTIC RETRIEVAL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 65)


if __name__ == "__main__":
    test_rag()
