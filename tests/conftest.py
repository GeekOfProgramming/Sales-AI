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
def default_test_env():
    """
    Ensure standard test sender environment variable is present for tests
    unless explicitly unset by monkeypatch in adversarial tests.
    """
    orig_sender = os.environ.get("SMTP_FROM_EMAIL")
    if not orig_sender:
        os.environ["SMTP_FROM_EMAIL"] = "outreach@pybim.com"
    yield
    if orig_sender is None:
        os.environ.pop("SMTP_FROM_EMAIL", None)
    else:
        os.environ["SMTP_FROM_EMAIL"] = orig_sender
