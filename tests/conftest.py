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
    orig_sender = os.environ.get("SMTP_FROM_EMAIL")
    if not orig_sender:
        os.environ["SMTP_FROM_EMAIL"] = "outreach@pybim.com"

    # Anchor Phase 5 lead aggregation time to test snapshot baseline (2026-09-30)
    # only for Phase 5 acceptance tests where fixed snapshot dates (2026-09-28) are used
    if "test_phase5_" in request.node.name or "test_phase5_acceptance" in str(request.node.fspath):
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
    if orig_sender is None:
        os.environ.pop("SMTP_FROM_EMAIL", None)
    else:
        os.environ["SMTP_FROM_EMAIL"] = orig_sender

