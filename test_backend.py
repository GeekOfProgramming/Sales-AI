"""Test suite for pyBIM-LLM FastAPI Backend Gateway.
Tests health endpoint, RAG retrieval, and AI code generation endpoints.
"""

import sys
import io
from fastapi.testclient import TestClient

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from backend.main import app


def test_gateway():
    print("=" * 65)
    print("🌐 FastAPI Gateway - Endpoints Verification")
    print("=" * 65)

    with TestClient(app) as client:
        # 1. Test Root Endpoint (Serves Web UI Dashboard)
        print("\n🔹 Test 1: GET / (Root Web UI)")
        res_root = client.get("/")
        print(f"Status Code: {res_root.status_code}")
        assert res_root.status_code == 200
        assert "pyBIM-LLM" in res_root.text
        print("✅ Test 1 Passed: Root correctly serves Web UI.")

        # 2. Test Health Endpoint
        print("\n" + "-" * 50)
        print("🔹 Test 2: GET /health")
        print("-" * 50)
        res_health = client.get("/health")
        print(f"Status Code: {res_health.status_code}")
        health_data = res_health.json()
        print("Health Status:", health_data)
        assert res_health.status_code == 200
        assert health_data["status"] == "healthy"
        assert health_data["ollama_connected"] is True
        assert health_data["chromadb_connected"] is True
        assert health_data["indexed_rules_count"] > 0
        print("✅ Test 2 Passed: Ollama & ChromaDB are both healthy and connected.")

        # 3. Test Generate Script for Python (pyRevit) with Element Metadata
        print("\n" + "-" * 50)
        print("🔹 Test 3: POST /generate-script (Python / pyRevit + Selected Elements)")
        print("-" * 50)
        payload_py = {
            "user_prompt": "Set the FireRating parameter of all selected walls to '2 Hours' inside a safe Transaction.",
            "language": "python",
            "selected_elements": [
                {
                    "element_id": 105234,
                    "category": "OST_Walls",
                    "name": "Generic - 200mm",
                    "parameters": {"FireRating": "None", "Length": 5.4},
                },
                {
                    "element_id": 105235,
                    "category": "OST_Walls",
                    "name": "Generic - 200mm",
                    "parameters": {"FireRating": "None", "Length": 3.8},
                },
            ],
            "include_rag_rules": True,
            "temperature": 0.1,
        }

        print("Sending request to /generate-script...")
        res_script = client.post("/generate-script", json=payload_py)
        print(f"Status Code: {res_script.status_code}")
        script_data = res_script.json()

        assert res_script.status_code == 200
        assert script_data["success"] is True
        print(f"⏱️ Total Execution Time: {script_data['execution_time_seconds']}s")
        print(f"📚 RAG Sources Consulted: {script_data['retrieved_sources']}")
        print(f"🔍 Validation Notes: {script_data['validation_notes']}")
        print("\n📝 Generated Python Code:\n")
        print(script_data["code"])

        assert "Transaction" in script_data["code"], "Generated script must manage Transactions"
        print("\n✅ Test 3 Passed: Python pyRevit script successfully generated.")

        # 4. Test Generate Script for C# Revit API
        print("\n" + "-" * 50)
        print("🔹 Test 4: POST /generate-script (C# / Revit API)")
        print("-" * 50)
        payload_cs = {
            "user_prompt": "Write an IExternalCommand to count all doors in the active document.",
            "language": "csharp",
            "include_rag_rules": True,
            "temperature": 0.1,
        }

        res_cs = client.post("/generate-script", json=payload_cs)
        print(f"Status Code: {res_cs.status_code}")
        cs_data = res_cs.json()

        assert res_cs.status_code == 200
        assert cs_data["success"] is True
        print(f"⏱️ Total Execution Time: {cs_data['execution_time_seconds']}s")
        print(f"🔍 Validation Notes: {cs_data['validation_notes']}")
        print("\n📝 Generated C# Code:\n")
        print(cs_data["code"])

        assert "IExternalCommand" in cs_data["code"] or "Transaction" in cs_data["code"]
        print("\n✅ Test 4 Passed: C# Revit API command successfully generated.")

        # 5. Test Online Ingestion Endpoint (Admin Hub)
        print("\n" + "-" * 50)
        print("🔹 Test 5: POST /api/ingest (Admin Knowledge Ingestion)")
        print("-" * 50)
        payload_ingest = {
            "url": "https://docs.pyrevitlabs.io/develop/pyrevit/forms/",
            "slug": "test_endpoint_ingest"
        }
        res_ingest = client.post("/api/ingest", json=payload_ingest)
        print(f"Status Code: {res_ingest.status_code}")
        ingest_data = res_ingest.json()
        print("Ingest Response:", ingest_data)
        assert res_ingest.status_code == 202
        assert ingest_data["status"] == "accepted"
        assert "Ingestion started in background" in ingest_data["message"]
        print("✅ Test 5 Passed: /api/ingest endpoint returns 202 Accepted and queues task.")

        # 6. Test Generate Script for Dynamo Python
        print("\n" + "-" * 50)
        print("🔹 Test 6: POST /generate-script (Dynamo Python Script Node)")
        print("-" * 50)
        payload_dyn = {
            "user_prompt": "Set FireRating parameter of input elements to '2 Hours' using TransactionManager.",
            "environment": "dynamo",
            "language": "python",
            "include_rag_rules": False,
            "temperature": 0.1,
        }
        res_dyn = client.post("/generate-script", json=payload_dyn)
        print(f"Status Code: {res_dyn.status_code}")
        dyn_data = res_dyn.json()
        print("Generated Dynamo Code:\n", dyn_data["code"])
        assert res_dyn.status_code == 200
        assert dyn_data["success"] is True
        # Check Dynamo protocol contracts
        code_str = dyn_data["code"]
        assert "RevitServices" in code_str or "DocumentManager" in code_str or "TransactionManager" in code_str or "OUT" in code_str
        print("✅ Test 6 Passed: Dynamo Python environment generated valid node code.")

    print("\n" + "=" * 65)
    print("🎉 ALL BACKEND ENDPOINTS PASSED TESTS SUCCESSFULLY!")
    print("=" * 65)


if __name__ == "__main__":
    test_gateway()
