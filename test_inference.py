"""Smoke test for local AI inference using BIMLLMClient and qwen2.5-coder:1.5b.
Validates code generation for both Revit Python (pyRevit) and Revit C# API.
"""

import sys
import io

# Ensure UTF-8 output encoding for Windows PowerShell/CMD
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest


def test_inference():
    print("=" * 60)
    print("🤖 BIM AI Core - Local Inference Test")
    print("=" * 60)

    # 1. Health check
    client = BIMLLMClient()
    if not client.is_healthy():
        print("❌ ERROR: Cannot reach local Ollama instance on port 11434.")
        sys.exit(1)

    print("✅ Ollama connection verified.")
    print(f"📦 Installed models: {client.list_available_models()}")

    # 2. Test Revit Python Code Generation (pyRevit)
    print("\n" + "-" * 50)
    print("🧪 Test 1: Generating Revit Python (pyRevit) Script...")
    print("-" * 50)

    req_python = CodeGenerationRequest(
        user_prompt="Get all walls in the active document using FilteredElementCollector, calculate total wall count, and print their element IDs.",
        language="python",
        temperature=0.1,
    )

    resp_python = client.generate_code(req_python)
    print(f"⏱️ Generation time: {resp_python.duration_seconds}s (Model: {resp_python.model})")
    print("📝 Extracted Python Code:\n")
    print(resp_python.extracted_code)

    assert "FilteredElementCollector" in resp_python.extracted_code, "Generated code should use FilteredElementCollector"
    print("\n✅ Test 1 Passed: FilteredElementCollector found in generated code.")

    # 3. Test Revit C# Code Generation (IExternalCommand)
    print("\n" + "-" * 50)
    print("🧪 Test 2: Generating Revit C# (.NET) Command...")
    print("-" * 50)

    req_csharp = CodeGenerationRequest(
        user_prompt="Write an IExternalCommand in C# that creates a new Wall in the active Revit document inside a Transaction.",
        language="csharp",
        temperature=0.1,
    )

    resp_csharp = client.generate_code(req_csharp)
    print(f"⏱️ Generation time: {resp_csharp.duration_seconds}s (Model: {resp_csharp.model})")
    print("📝 Extracted C# Code:\n")
    print(resp_csharp.extracted_code)

    assert "IExternalCommand" in resp_csharp.extracted_code or "Transaction" in resp_csharp.extracted_code, "Generated code should contain Revit API types"
    print("\n✅ Test 2 Passed: Revit API structures found in generated C# code.")

    print("\n" + "=" * 60)
    print("🎉 ALL INFERENCE TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    test_inference()
