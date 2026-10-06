from datetime import datetime
import os
import sys
import types
import pytest

try:
    import ollama
except ImportError:
    mock_ollama = types.ModuleType("ollama")
    mock_ollama.Client = None
    sys.modules["ollama"] = mock_ollama


@pytest.fixture(autouse=True)
def default_test_env(request, monkeypatch):
    """
    Ensure standard test sender environment variable is present for tests
    unless explicitly unset by monkeypatch in adversarial tests.
    """
    fspath_str = str(request.node.fspath)
    if "test_phase10_5" in fspath_str:
        monkeypatch.setenv("SMTP_FROM_EMAIL", "sales@pybim.it")
        monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "sales@pybim.it,outreach@pybim.com")
        monkeypatch.setenv("OUTREACH_SENDER_EMAIL", "sales@pybim.it")
    else:
        # Default sender for Phase 9 and earlier test suites
        if "SMTP_FROM_EMAIL" not in os.environ or os.environ.get("SMTP_FROM_EMAIL") == "sales@pybim.it":
            monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
        if "SMTP_ALLOWED_SENDERS" not in os.environ or os.environ.get("SMTP_ALLOWED_SENDERS") == "sales@pybim.it":
            monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "outreach@pybim.com,sales@pybim.it")

    # Anchor Phase 5 lead aggregation time to test snapshot baseline (2026-09-30)
    # only for Phase 5 acceptance tests where fixed snapshot dates (2026-09-28) are used
    if "test_phase5_" in request.node.name or "test_phase5_acceptance" in fspath_str:
        try:
            import sales_engine.leads.lead_aggregator as la
            class FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2026, 9, 30, 12, 0, 0)
            monkeypatch.setattr(la, "datetime", FixedDateTime)
        except Exception:
            pass

    yield


