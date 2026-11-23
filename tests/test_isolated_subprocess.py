"""
tests/test_isolated_subprocess.py
Mocked unit tests for fail-closed subprocess isolation helper.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tests.support.isolated_subprocess import (
    _REPO_ROOT,
    run_isolated_subprocess,
)


@patch("subprocess.run")
def test_production_path_rejection(mock_run: MagicMock) -> None:
    """Verify direct, repo-relative, backup, and unresolved '..' operational paths raise PermissionError."""
    protected_paths = [
        r"C:\Sales-AI\SalesAI\data\sales_outreach.db",
        r"c:/sales-ai/salesai/data/sales_outreach.db",
        str(_REPO_ROOT / "data" / "sales_outreach.db"),
        # Deliberately unresolved '..' traversal to verify helper normalization
        str(_REPO_ROOT / "data" / "backups" / ".." / "sales_outreach.db"),
        str(_REPO_ROOT / "data" / "backups" / "sales_outreach_2026.db"),
        str(_REPO_ROOT / "data" / "sales_outreach.db.bak"),
        str(_REPO_ROOT / "data" / "sales_outreach.db.backup"),
        str(_REPO_ROOT / "data" / "sales_outreach.db-wal"),
    ]
    for p in protected_paths:
        with pytest.raises(PermissionError) as exc_info:
            run_isolated_subprocess(["python", "--version"], isolated_db_path=p)
        assert "CRITICAL TEST ISOLATION FAILURE" in str(exc_info.value)
    mock_run.assert_not_called()


@patch("subprocess.run")
def test_arbitrary_outside_temp_path_rejected(mock_run: MagicMock, tmp_path: Path) -> None:
    """Verify arbitrary absolute paths outside disposable temp/trusted directories fail closed."""
    outside_paths = [
        r"C:\arbitrary_outside\sales.db",
        str(_REPO_ROOT / "salesai.db"),
        str(_REPO_ROOT / "tests" / "temp_test.db"),
    ]
    for p in outside_paths:
        with pytest.raises(PermissionError):
            run_isolated_subprocess(["python", "--version"], isolated_db_path=p)

    # Also verify explicit trusted_dir containment
    trusted = tmp_path / "sandbox"
    trusted.mkdir()
    outside_via_traversal = str(trusted / ".." / "escaped.db")
    with pytest.raises(PermissionError):
        run_isolated_subprocess(
            ["python", "--version"],
            isolated_db_path=outside_via_traversal,
            trusted_dir=trusted,
        )

    mock_run.assert_not_called()


@patch("subprocess.run")
def test_invalid_trusted_dir_and_directory_target_rejected(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify dangerous trusted_dir boundaries and directory targets fail closed."""
    valid_db = tmp_path / "valid.db"

    for dangerous_dir in (_REPO_ROOT, _REPO_ROOT / "data", _REPO_ROOT.parent):
        with pytest.raises(PermissionError):
            run_isolated_subprocess(
                ["python", "--version"],
                isolated_db_path=valid_db,
                trusted_dir=dangerous_dir,
            )

    # Filesystem root must be rejected
    fs_root = Path(tmp_path.anchor)
    with pytest.raises(ValueError):
        run_isolated_subprocess(
            ["python", "--version"],
            isolated_db_path=valid_db,
            trusted_dir=fs_root,
        )

    # Existing directory passed as isolated_db_path must be rejected
    sub_dir = tmp_path / "not_a_file"
    sub_dir.mkdir()
    with pytest.raises(ValueError):
        run_isolated_subprocess(
            ["python", "--version"],
            isolated_db_path=sub_dir,
            trusted_dir=tmp_path,
        )

    mock_run.assert_not_called()


@patch("subprocess.run")
def test_symlink_or_junction_escape_rejected(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify resolvable symlink/junction targets pointing to production or outside dir fail closed."""
    fake_link_in_tmp = tmp_path / "symlink_to_prod.db"
    prod_target = (_REPO_ROOT / "data" / "sales_outreach.db").resolve()
    orig_resolve = Path.resolve

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        if os.path.normcase(str(self)) == os.path.normcase(str(fake_link_in_tmp)):
            return prod_target
        return orig_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    with pytest.raises(PermissionError):
        run_isolated_subprocess(["python", "--version"], isolated_db_path=fake_link_in_tmp)
    mock_run.assert_not_called()


@patch("subprocess.run")
def test_relative_empty_uri_and_malformed_path_rejection(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify empty, whitespace-padded, relative, :memory:, URI, UNC, ADS, and NUL paths fail closed."""
    invalid_values = [
        "",
        "   ",
        f" {tmp_path / 'padded.db'}",
        f"{tmp_path / 'padded.db'} ",
        "relative/path.db",
        "sales_outreach.db",
        ":memory:",
        ":MEMORY:",
        "file:test.db?mode=ro",
        "FILE:///C:/temp/test.db",
        f"{tmp_path / 'query.db'}?mode=ro",
        r"\\?\C:\temp\ext.db",
        r"\\server\share\test.db",
        f"{tmp_path / 'ads.db'}:stream",
        f"{tmp_path / 'nul.db'}\x00extra",
    ]
    for p in invalid_values:
        with pytest.raises(ValueError):
            run_isolated_subprocess(["python", "--version"], isolated_db_path=p)

    for bad_type in (None, b"C:\\temp\\bytes.db", 12345):
        with pytest.raises(TypeError):
            run_isolated_subprocess(["python", "--version"], isolated_db_path=bad_type)  # type: ignore[arg-type]

    mock_run.assert_not_called()


@patch("subprocess.run")
def test_command_validation_rejects_strings_and_invalid_sequences(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify cmd as str/bytes, empty sequences, non-str items, NUL bytes, and whitespace exes fail."""
    test_db = tmp_path / "valid_test.db"

    bad_type_cmds = [
        "python --version",
        b"python --version",
        bytearray(b"python"),
        None,
        123,
        {"python", "--version"},
        ["python", 123],
        ["python", None],
        ["python", b"--version"],
        ["python", test_db],
    ]
    for bad_cmd in bad_type_cmds:
        with pytest.raises(TypeError):
            run_isolated_subprocess(bad_cmd, isolated_db_path=test_db)  # type: ignore[arg-type]

    bad_val_cmds = [
        [],
        (),
        [""],
        ["   "],
        ["python", "arg\x00with_nul"],
    ]
    for bad_cmd in bad_val_cmds:
        with pytest.raises(ValueError):
            run_isolated_subprocess(bad_cmd, isolated_db_path=test_db)

    mock_run.assert_not_called()


@patch("subprocess.run")
def test_list_and_tuple_commands_preserved(mock_run: MagicMock, tmp_path: Path) -> None:
    """Verify valid list and tuple commands pass validation intact."""
    test_db = tmp_path / "valid_test.db"
    mock_run.return_value = subprocess.CompletedProcess(args=["ok"], returncode=0)

    run_isolated_subprocess(["python", "--version"], isolated_db_path=test_db)
    run_isolated_subprocess(("python", "--version"), isolated_db_path=test_db)
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0] == ["python", "--version"]
    assert mock_run.call_args_list[1].args[0] == ("python", "--version")


@patch("subprocess.run")
def test_shell_non_false_and_env_kwarg_rejected(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify shell=True/non-False and direct env= kwarg are rejected; shell=False is accepted."""
    test_db = tmp_path / "valid_test.db"
    for bad_shell in (True, 1, "yes", None):
        with pytest.raises(ValueError) as exc_info:
            run_isolated_subprocess(
                ["python", "--version"],
                isolated_db_path=test_db,
                shell=bad_shell,
            )
        assert "shell" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_env:
        run_isolated_subprocess(
            ["python", "--version"],
            isolated_db_path=test_db,
            env={"FOO": "BAR"},
        )
    assert "Passing 'env' directly" in str(exc_env.value)
    mock_run.assert_not_called()

    mock_run.return_value = subprocess.CompletedProcess(args=["ok"], returncode=0)
    run_isolated_subprocess(["python", "--version"], isolated_db_path=test_db, shell=False)
    mock_run.assert_called_once()


@patch("subprocess.run")
def test_all_three_env_vars_populated_and_unrelated_preserved(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify all three DB vars equal resolved isolated_db_path and extra_env preserves others."""
    test_db = tmp_path / "valid_test.db"
    mock_run.return_value = subprocess.CompletedProcess(args=["test"], returncode=0, stdout="OK")

    res = run_isolated_subprocess(
        ["python", "--version"],
        isolated_db_path=test_db,
        extra_env={"CUSTOM_APP_FLAG": "keep_me"},
    )
    assert res.returncode == 0
    mock_run.assert_called_once()

    child_env = mock_run.call_args.kwargs["env"]
    expected_str = str(test_db.resolve())
    assert child_env["SALES_OUTREACH_DB"] == expected_str
    assert child_env["SALESAI_DB_PATH"] == expected_str
    assert child_env["SALESAI_CANDIDATES_DB"] == expected_str
    assert child_env["CUSTOM_APP_FLAG"] == "keep_me"


@patch("subprocess.run")
def test_windows_case_insensitive_env_cleanup_and_canonicalization(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify lowercase/mixed-case DB keys in inherited env or matching extra_env are canonicalized."""
    test_db = tmp_path / "valid_test.db"
    mock_run.return_value = subprocess.CompletedProcess(args=["ok"], returncode=0)

    fake_parent_env = {
        "PATH": os.environ.get("PATH", ""),
        "sales_outreach_db": str(_REPO_ROOT / "data" / "sales_outreach.db"),
        "SalesAI_Db_Path": r"C:\other\stale.db",
        "custom_var": "old",
    }
    monkeypatch.setattr(os, "environ", fake_parent_env)

    run_isolated_subprocess(
        ["python", "--version"],
        isolated_db_path=test_db,
        extra_env={
            "salesai_candidates_db": str(test_db),
            "CUSTOM_VAR": "new",
        },
    )

    mock_run.assert_called_once()
    child_env = mock_run.call_args.kwargs["env"]
    expected_str = str(test_db.resolve())

    # Stale lowercase/mixed-case DB keys must be gone; canonical uppercase keys set consistently
    assert "sales_outreach_db" not in child_env
    assert "SalesAI_Db_Path" not in child_env
    assert "salesai_candidates_db" not in child_env
    assert child_env["SALES_OUTREACH_DB"] == expected_str
    assert child_env["SALESAI_DB_PATH"] == expected_str
    assert child_env["SALESAI_CANDIDATES_DB"] == expected_str

    if os.name == "nt":
        assert "custom_var" not in child_env
        assert child_env["CUSTOM_VAR"] == "new"

    # Mocked parent env was not mutated
    assert fake_parent_env["sales_outreach_db"].endswith("sales_outreach.db")


@patch("subprocess.run")
def test_conflicting_or_invalid_extra_env_rejected(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify conflicting/invalid extra_env DB values and duplicate keys fail closed."""
    test_db = tmp_path / "valid_test.db"
    other_db = tmp_path / "conflict.db"

    conflicting_envs = [
        {"SALES_OUTREACH_DB": str(other_db)},
        {"sales_outreach_db": str(other_db)},
        {"SALESAI_DB_PATH": str(other_db)},
        {"SalesAI_Db_Path": str(other_db)},
        {"SALESAI_CANDIDATES_DB": str(other_db)},
        {"SALESAI_DB_PATH": "relative.db"},
        {"SALES_OUTREACH_DB": str(_REPO_ROOT / "data" / "sales_outreach.db")},
    ]
    for env_dict in conflicting_envs:
        with pytest.raises((ValueError, PermissionError)):
            run_isolated_subprocess(
                ["python", "--version"],
                isolated_db_path=test_db,
                extra_env=env_dict,
            )

    if os.name == "nt":
        with pytest.raises(ValueError):
            run_isolated_subprocess(
                ["python", "--version"],
                isolated_db_path=test_db,
                extra_env={"FOO": "1", "foo": "2"},
            )

    with pytest.raises(TypeError):
        run_isolated_subprocess(
            ["python", "--version"],
            isolated_db_path=test_db,
            extra_env=["NOT_A_MAPPING"],  # type: ignore[arg-type]
        )

    mock_run.assert_not_called()


@patch("subprocess.run")
def test_cli_db_path_mismatch_and_malformed_rejected(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify mismatch, operational target, missing value, whitespace, and option-as-value fail closed."""
    test_db = tmp_path / "valid_test.db"
    mismatch_db = tmp_path / "mismatch.db"

    bad_value_cmds = [
        ["script.py", "--db-path", str(mismatch_db)],
        ["script.py", f"--db-path={str(mismatch_db)}"],
        ["script.py", "--DB-PATH", str(mismatch_db)],
        ["script.py", "--db-path"],
        ["script.py", "--db-path", ""],
        ["script.py", "--db-path", "   "],
        ["script.py", "--db-path="],
        ["script.py", "--db-path=   "],
        ["script.py", "--db-path", f" {test_db} "],
        ["script.py", f"--db-path= {test_db}"],
        ["script.py", "--db-path", "--live-connect"],
        ["script.py", "--db-path=--live-connect"],
        ["script.py", "--db-path", str(test_db), f"--db-path={str(mismatch_db)}"],
    ]
    for bad_cmd in bad_value_cmds:
        with pytest.raises(ValueError):
            run_isolated_subprocess(bad_cmd, isolated_db_path=test_db)

    with pytest.raises(PermissionError):
        run_isolated_subprocess(
            ["script.py", "--db-path", str(_REPO_ROOT / "data" / "sales_outreach.db")],
            isolated_db_path=test_db,
        )

    mock_run.assert_not_called()


@patch("subprocess.run")
def test_both_cli_db_path_syntaxes_supported(mock_run: MagicMock, tmp_path: Path) -> None:
    """Verify both '--db-path <val>' and '--db-path=<val>' work when matching isolated_db_path."""
    test_db = tmp_path / "valid_test.db"
    mock_run.return_value = subprocess.CompletedProcess(args=["test"], returncode=0)

    run_isolated_subprocess(
        ["script.py", "--db-path", str(test_db)],
        isolated_db_path=test_db,
    )
    run_isolated_subprocess(
        ["script.py", f"--db-path={str(test_db)}"],
        isolated_db_path=test_db,
    )
    assert mock_run.call_count == 2


@patch("subprocess.run")
def test_parent_os_environ_not_mutated(mock_run: MagicMock, tmp_path: Path) -> None:
    """Verify parent process os.environ is NOT mutated by running the helper."""
    test_db = tmp_path / "valid_test.db"
    mock_run.return_value = subprocess.CompletedProcess(args=["test"], returncode=0)

    before = dict(os.environ)
    run_isolated_subprocess(
        ["python", "--version"],
        isolated_db_path=test_db,
        extra_env={"EPHEMERAL_CHILD_ONLY": "1"},
    )
    assert dict(os.environ) == before


@patch("subprocess.run")
def test_valid_subprocess_arguments_return_and_exception_preserved(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify extra kwargs, return values, and CalledProcessError propagation are preserved."""
    test_db = tmp_path / "valid_test.db"
    mock_proc = subprocess.CompletedProcess(
        args=["python", "-c", "print(1)"],
        returncode=0,
        stdout="1\n",
        stderr="",
    )
    mock_run.return_value = mock_proc

    res = run_isolated_subprocess(
        ["python", "-c", "print(1)"],
        isolated_db_path=test_db,
        trusted_dir=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        cwd=str(tmp_path),
    )

    assert res is mock_proc
    assert res.stdout == "1\n"
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["capture_output"] is True
    assert mock_run.call_args.kwargs["text"] is True
    assert mock_run.call_args.kwargs["check"] is False
    assert mock_run.call_args.kwargs["timeout"] == 5
    assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)

    mock_run.side_effect = subprocess.CalledProcessError(returncode=3, cmd=["python"])
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        run_isolated_subprocess(["python"], isolated_db_path=test_db, check=True)
    assert exc_info.value.returncode == 3
