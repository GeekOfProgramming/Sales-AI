"""Test suite for Revit Plugin & Bridge (pyRevit & C# Addin).
Simulates Revit client payloads against the FastAPI Gateway to ensure end-to-end integration.
"""

import sys
import io
from fastapi.testclient import TestClient

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from backend.main import app


def test_revit_bridge():
    print("=" * 65)
    print("🔌 Revit Integration Bridge - Simulation Tests")
    print("=" * 65)

    with TestClient(app) as client:
        # 1. Simulate pyRevit Tool Interaction
        print("\n🔹 Test 1: Simulating pyRevit Tool Interaction...")
        pyrevit_payload = {
            "user_prompt": "Set FireRating parameter to '2 Hours' for the selected walls and verify using transactions.",
            "language": "python",
            "selected_elements": [
                {"element_id": 482910, "category": "OST_Walls", "name": "Basic Wall - Generic 200mm"},
                {"element_id": 482911, "category": "OST_Walls", "name": "Basic Wall - Generic 200mm"},
            ],
            "include_rag_rules": True,
            "temperature": 0.1,
        }

        res_py = client.post("/generate-script", json=pyrevit_payload)
        print(f"pyRevit Call Status: {res_py.status_code}")
        data_py = res_py.json()

        assert res_py.status_code == 200
        assert data_py["success"] is True
        print(f"⏱️ Inference Duration: {data_py['execution_time_seconds']}s")
        print(f"📚 RAG Rules Consulted: {data_py['retrieved_sources']}")
        print("📝 Code Preview (first 150 chars):\n", data_py["code"][:150], "...\n")
        assert "Transaction" in data_py["code"]
        print("✅ Test 1 Passed: pyRevit client flow successfully simulated and validated.")

        # 2. Simulate Native C# Addin Interaction
        print("\n" + "-" * 50)
        print("🔹 Test 2: Simulating Native C# IExternalCommand Interaction...")
        print("-" * 50)
        csharp_payload = {
            "user_prompt": "Create an IExternalCommand that renames views according to ISO 19650 architectural standard.",
            "language": "csharp",
            "selected_elements": [
                {"element_id": 501230, "category": "OST_Views", "name": "Level 1 - Architectural"},
            ],
            "include_rag_rules": True,
            "temperature": 0.1,
        }

        res_cs = client.post("/generate-script", json=csharp_payload)
        print(f"C# Addin Call Status: {res_cs.status_code}")
        data_cs = res_cs.json()

        assert res_cs.status_code == 200
        assert data_cs["success"] is True
        print(f"⏱️ Inference Duration: {data_cs['execution_time_seconds']}s")
        print(f"📚 RAG Rules Consulted: {data_cs['retrieved_sources']}")
        print("📝 Code Preview (first 150 chars):\n", data_cs["code"][:150], "...\n")
        assert "IExternalCommand" in data_cs["code"] or "Transaction" in data_cs["code"]
        print("✅ Test 2 Passed: Native C# Addin client flow successfully simulated and validated.")

    print("\n" + "=" * 65)
    print("🎉 ALL REVIT INTEGRATION BRIDGE TESTS PASSED SUCCESSFULLY!")
    print("=" * 65)


if __name__ == "__main__":
    test_revit_bridge()
