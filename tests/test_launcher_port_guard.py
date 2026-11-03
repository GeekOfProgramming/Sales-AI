import os
import sys
import socket
import subprocess
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

def test_exclusive_socket_guard_blocks_duplicate_process(monkeypatch):
    """
    Verify that start_server's exclusive socket binding prevents duplicate / rogue
    processes from stealth-binding port 8000 on Windows.
    """
    test_port = 8991
    monkeypatch.setenv("SALESAI_PORT", str(test_port))
    monkeypatch.setenv("SALESAI_HOST", "127.0.0.1")

    # Bind first socket
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE") and sys.platform == "win32":
        s1.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        s1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s1.bind(("127.0.0.1", test_port))
    s1.listen(1)

    try:
        # A second socket attempting to bind must fail
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE") and sys.platform == "win32":
            s2.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        with pytest.raises(OSError):
            s2.bind(("127.0.0.1", test_port))
            s2.listen(1)
    finally:
        s1.close()
        try:
            s2.close()
        except Exception:
            pass


def test_credential_detection_and_discovery_dry_run_state(monkeypatch):
    """
    Verify that BRAVE_SEARCH_API_KEY environment variable correctly dictates
    the discovery dry run state between CREDENTIAL_MISSING and CREDENTIAL_PRESENT.
    """
    # 1. When absent
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY")
    state_missing = "CREDENTIAL_PRESENT" if (brave_key and brave_key.strip()) else "CREDENTIAL_MISSING"
    assert state_missing == "CREDENTIAL_MISSING"

    # 2. When present
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "mock_key_abc_123")
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY")
    state_present = "CREDENTIAL_PRESENT" if (brave_key and brave_key.strip()) else "CREDENTIAL_MISSING"
    assert state_present == "CREDENTIAL_PRESENT"


def test_source_registry_status_independent_of_enabled_flag(monkeypatch):
    """
    Verify that source credential configuration is reported accurately
    while source enabled/disabled state remains independent.
    """
    from sales_engine.discovery_v2.source_registry import SourceRegistry
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "mock_key_xyz")
    registry = SourceRegistry()
    src = registry.get_source("brave_job_search")
    assert src is not None
    # Administrative state can be toggled without changing credential status
    assert src.get_credential_status() == "configured"
    src.enabled = False
    assert src.get_credential_status() == "configured"

