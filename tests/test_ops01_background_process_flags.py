"""
tests/test_ops01_background_process_flags.py
Focused offline regression tests for OPS-01 Windows background process console suppression.

Verifies:
- Windows background subprocesses pass CREATE_NO_WINDOW without altering arguments or using a shell.
- Non-Windows fallback behavior remains intact.
- Timeout, exit-code, stdout/stderr capture, and debug error logging are preserved.
- Interactive operator launchers remain unchanged.
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sales_engine.inbox.credential_store import MailboxCredentialStore


def test_credential_store_windows_icacls_uses_create_no_window(tmp_path, monkeypatch):
    """Windows ACL hardening passes CREATE_NO_WINDOW, exact args, timeout, and no shell."""
    target = tmp_path / ".mailbox_credentials.enc"
    target.write_bytes(b"dummy")
    store = MailboxCredentialStore(store_path=target)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "test_operator")

    expected_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", expected_flag, raising=False)

    with patch("subprocess.run") as mock_run:
        store._restrict_file_permissions(target)

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == ["icacls", str(target), "/inheritance:r", "/grant:r", "test_operator:(F)"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 5.0
    assert kwargs["creationflags"] == expected_flag
    assert not kwargs.get("shell", False)


def test_credential_store_non_windows_uses_chmod_without_subprocess(tmp_path, monkeypatch):
    """Non-Windows platforms continue using POSIX 0o600 permissions without subprocess."""
    target = tmp_path / ".mailbox_credentials.enc"
    target.write_bytes(b"dummy")
    store = MailboxCredentialStore(store_path=target)

    monkeypatch.setattr(sys, "platform", "linux")

    with patch("subprocess.run") as mock_run, patch("os.chmod") as mock_chmod:
        store._restrict_file_permissions(target)

    mock_run.assert_not_called()
    mock_chmod.assert_called_once_with(str(target), 0o600)


def test_credential_store_icacls_timeout_logs_debug_and_preserves_roundtrip(tmp_path, monkeypatch, caplog):
    """Timeout or failure in icacls logs at debug level without crashing credential save."""
    target = tmp_path / ".mailbox_credentials.enc"
    store = MailboxCredentialStore(store_path=target)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "test_operator")

    with caplog.at_level(logging.DEBUG, logger="sales_engine.inbox.credential_store"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["icacls"], timeout=5.0)):
            store.set_credential("win_cred_test", "synthetic_secret_value")

    assert any("Could not apply strict ACLs" in rec.message for rec in caplog.records)
    assert store.get_credential("win_cred_test") == "synthetic_secret_value"


def _import_capture_script(monkeypatch):
    """Import scripts.capture_ux01_screenshots safely with stubbed playwright."""
    fake_playwright_pkg = types.ModuleType("playwright")
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = MagicMock()
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    mod_name = "scripts.capture_ux01_screenshots"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


def test_capture_ux01_windows_popen_uses_create_no_window(monkeypatch):
    """Background uvicorn child server in capture_ux01_screenshots uses CREATE_NO_WINDOW on Windows."""
    mod = _import_capture_script(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")

    expected_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", expected_flag, raising=False)

    mock_proc = MagicMock()
    mock_browser = MagicMock()
    mock_pw_inst = MagicMock()
    mock_pw_inst.chromium.launch.return_value = mock_browser

    with (
        patch.object(mod.subprocess, "Popen", return_value=mock_proc) as mock_popen,
        patch.object(mod, "sync_playwright") as mock_sync_pw,
        patch.object(mod.time, "sleep"),
        patch.object(mod, "VIEWPORTS", []),
        patch.object(mod, "PAGES_TO_CAPTURE", []),
    ):
        mock_sync_pw.return_value.start.return_value = mock_pw_inst
        mod.run_capture()

    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    assert args[0] == [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8899",
    ]
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["creationflags"] == expected_flag
    assert not kwargs.get("shell", False)
    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=5)


def test_capture_ux01_non_windows_omits_creationflags(monkeypatch):
    """Non-Windows execution of capture_ux01_screenshots does not pass Windows creationflags."""
    mod = _import_capture_script(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")

    mock_proc = MagicMock()
    mock_browser = MagicMock()
    mock_pw_inst = MagicMock()
    mock_pw_inst.chromium.launch.return_value = mock_browser

    with (
        patch.object(mod.subprocess, "Popen", return_value=mock_proc) as mock_popen,
        patch.object(mod, "sync_playwright") as mock_sync_pw,
        patch.object(mod.time, "sleep"),
        patch.object(mod, "VIEWPORTS", []),
        patch.object(mod, "PAGES_TO_CAPTURE", []),
    ):
        mock_sync_pw.return_value.start.return_value = mock_pw_inst
        mod.run_capture()

    mock_popen.assert_called_once()
    _, kwargs = mock_popen.call_args
    assert "creationflags" not in kwargs
    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=5)


def test_synthetic_child_process_preserves_exit_code_streams_and_timeout():
    """Harmless synthetic child process under CREATE_NO_WINDOW preserves exit code, stdout/stderr, and timeout."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    res = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; print('stdout_ok'); sys.stderr.write('stderr_ok\\n'); sys.exit(7)",
        ],
        capture_output=True,
        text=True,
        timeout=10.0,
        creationflags=flags,
    )
    assert res.returncode == 7
    assert "stdout_ok" in res.stdout
    assert "stderr_ok" in res.stderr

    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            capture_output=True,
            text=True,
            timeout=0.2,
            creationflags=flags,
        )


def test_interactive_launcher_files_preserved():
    """Interactive operator launchers remain intact and non-suppressed."""
    ps1 = (REPO_ROOT / "run_sales_ai.ps1").read_text(encoding="utf-8")
    bat = (REPO_ROOT / "run_sales_ai.bat").read_text(encoding="utf-8")
    assert "& $PythonExe -u start_server.py" in ps1
    assert "run_sales_ai.ps1" in bat
    assert "CREATE_NO_WINDOW" not in ps1
    assert "SW_HIDE" not in ps1
