"""Unit and integration tests for LAN-01 Automatic Local Network URL & Launcher.
Tests RFC 1918 detection, PowerShell launcher syntax, origin/CSRF validation over LAN IP,
and settings view reflection.
"""

import os
import sys
import socket
import ipaddress
import subprocess
from pathlib import Path
import pytest
from starlette.requests import Request
from starlette.datastructures import Headers

REPO_ROOT = Path(__file__).parent.parent


def test_powershell_launcher_syntax():
    """Verify run_sales_ai.ps1 has zero syntax errors according to the PowerShell parser."""
    launcher_path = REPO_ROOT / "run_sales_ai.ps1"
    assert launcher_path.exists(), "run_sales_ai.ps1 must exist"

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        f"& {{ $errors = $null; [System.Management.Automation.Language.Parser]::ParseInput((Get-Content '{str(launcher_path)}' -Raw), [ref]$null, [ref]$errors); if ($errors) {{ exit 1 }} else {{ exit 0 }} }}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"PowerShell parser found errors: {res.stdout} {res.stderr}"


def test_rfc1918_private_ip_filtering(monkeypatch):
    """Verify get_local_ips filters only RFC 1918 private IPv4 addresses."""
    from start_server import get_local_ips

    mock_ips = [
        "127.0.0.1",          # Loopback (excluded)
        "169.254.1.2",        # Link-local APIPA (excluded)
        "8.8.8.8",            # Public IPv4 (excluded)
        "1.1.1.1",            # Public IPv4 (excluded)
        "192.168.1.50",       # RFC 1918 (included)
        "10.0.5.12",          # RFC 1918 (included)
        "172.20.10.4",        # RFC 1918 (included)
        "172.35.1.1",         # Outside 172.16-31 (excluded)
    ]

    monkeypatch.setattr(socket, "gethostname", lambda: "mock-host")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda name: ("mock-host", [], mock_ips))

    result = get_local_ips()
    assert "192.168.1.50" in result
    assert "10.0.5.12" in result
    assert "172.20.10.4" in result
    assert "127.0.0.1" not in result
    assert "169.254.1.2" not in result
    assert "8.8.8.8" not in result
    assert "1.1.1.1" not in result
    assert "172.35.1.1" not in result


def test_same_origin_validation_over_lan_ip():
    """Verify is_same_origin accepts requests originating from LAN IP address."""
    from backend.sales_ui.router import is_same_origin

    lan_ip = "172.20.10.4"
    lan_host = f"{lan_ip}:8000"
    lan_origin = f"http://{lan_ip}:8000"

    # Request with Host and Origin matching LAN IP
    req = Request({
        "type": "http",
        "method": "POST",
        "headers": [
            (b"host", lan_host.encode()),
            (b"origin", lan_origin.encode()),
        ],
    })
    assert is_same_origin(req) is True

    # Request with Host matching LAN IP and Referer matching LAN URL
    req_referer = Request({
        "type": "http",
        "method": "POST",
        "headers": [
            (b"host", lan_host.encode()),
            (b"referer", f"{lan_origin}/sales/candidates".encode()),
        ],
    })
    assert is_same_origin(req_referer) is True

    # Request with Host matching LAN IP but foreign untrusted Origin
    req_foreign = Request({
        "type": "http",
        "method": "POST",
        "headers": [
            (b"host", lan_host.encode()),
            (b"origin", b"http://malicious-site.com"),
        ],
    })
    assert is_same_origin(req_foreign) is False


def test_sales_ui_settings_view_reflects_lan_configuration(monkeypatch):
    """Verify Settings view properly reflects LAN binding and LAN IP in template context."""
    from fastapi.testclient import TestClient
    from backend.main import app

    monkeypatch.setenv("SALESAI_HOST", "0.0.0.0")
    monkeypatch.setenv("SALESAI_PORT", "8000")
    monkeypatch.setenv("SALESAI_LAN_IP", "172.20.10.4")

    client = TestClient(app)
    response = client.get("/sales/settings", headers={"Host": "172.20.10.4:8000"})
    assert response.status_code == 200
    html = response.text

    assert "0.0.0.0:8000" in html
    assert "172.20.10.4" in html
    assert "Trusted LAN Active (0.0.0.0)" in html


def test_sales_ui_lan_host_navigation(monkeypatch):
    """Verify core UI endpoints load successfully when requested via LAN Host header."""
    from fastapi.testclient import TestClient
    from backend.main import app

    lan_ip = "172.20.10.4"
    lan_headers = {"Host": f"{lan_ip}:8000"}

    client = TestClient(app)

    # 1. Console Dashboard
    r_dashboard = client.get("/sales", headers=lan_headers)
    assert r_dashboard.status_code == 200
    assert "sales_csrf_token" in r_dashboard.cookies

    # 2. Qualified Leads
    r_leads = client.get("/sales/leads", headers=lan_headers)
    assert r_leads.status_code == 200

    # 3. Candidates
    r_candidates = client.get("/sales/candidates", headers=lan_headers)
    assert r_candidates.status_code == 200
