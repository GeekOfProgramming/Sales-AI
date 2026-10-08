"""Test suite verifying Web UI serving and API Gateway integration."""

import sys
import io
from fastapi.testclient import TestClient

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from backend.main import app


def test_web_ui():
    print("=" * 65)
    print("🎨 Web UI & Studio Serving Tests")
    print("=" * 65)

    with TestClient(app) as client:
        # 1. Test Root serves Web UI HTML
        print("\n🔹 Test 1: GET / (Web UI Index)")
        res_root = client.get("/")
        print(f"Status Code: {res_root.status_code}")
        assert res_root.status_code == 200
        assert "pyBIM-LLM Studio" in res_root.text
        assert "<!DOCTYPE html>" in res_root.text
        print("✅ Test 1 Passed: Root correctly serves HTML studio dashboard.")

        # 2. Test /ui dedicated endpoint
        print("\n" + "-" * 50)
        print("🔹 Test 2: GET /ui")
        print("-" * 50)
        res_ui = client.get("/ui")
        print(f"Status Code: {res_ui.status_code}")
        assert res_ui.status_code == 200
        assert "pyBIM-LLM Studio" in res_ui.text
        print("✅ Test 2 Passed: /ui route serves studio dashboard.")

        # 3. Test Health Endpoint for UI Widgets
        print("\n" + "-" * 50)
        print("🔹 Test 3: GET /health (Dashboard Widget Feed)")
        print("-" * 50)
        res_health = client.get("/health")
        assert res_health.status_code == 200
        data = res_health.json()
        print("Live Health Metrics:", data)
        assert data["status"] == "healthy"
        assert data["indexed_rules_count"] > 0
        print("✅ Test 3 Passed: Health metrics active for dashboard badge.")

    print("\n" + "=" * 65)
    print("🎉 ALL WEB UI & STUDIO SERVING TESTS PASSED!")
    print("=" * 65)


if __name__ == "__main__":
    test_web_ui()
