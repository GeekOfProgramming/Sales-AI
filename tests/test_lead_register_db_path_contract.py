"""
tests/test_lead_register_db_path_contract.py
Isolated regression tests for LeadRegisterStore explicit db_path constructor contract.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from sales_engine.leads.register_store import LeadRegisterStore


class _CustomPathLike:
    def __init__(self, path: str) -> None:
        self._path = path

    def __fspath__(self) -> str:
        return self._path


def test_missing_db_path_raises_before_sqlite_init() -> None:
    """Calling LeadRegisterStore() without db_path raises TypeError before _init_db or sqlite3.connect."""
    with (
        patch.object(LeadRegisterStore, "_init_db") as mock_init,
        patch("sales_engine.leads.register_store.sqlite3.connect") as mock_connect,
    ):
        with pytest.raises(TypeError):
            LeadRegisterStore()  # type: ignore[call-arg]
        mock_init.assert_not_called()
        mock_connect.assert_not_called()


@pytest.mark.parametrize("bad_path", [None, "", "   ", "\t\n"])
def test_none_empty_and_whitespace_rejected_before_sqlite_init(bad_path) -> None:
    """None, empty string, and whitespace-only db_path raise ValueError before _init_db or sqlite3.connect."""
    with (
        patch.object(LeadRegisterStore, "_init_db") as mock_init,
        patch("sales_engine.leads.register_store.sqlite3.connect") as mock_connect,
    ):
        with pytest.raises(ValueError):
            LeadRegisterStore(bad_path)
        mock_init.assert_not_called()
        mock_connect.assert_not_called()


def test_explicit_temporary_path_preserved_and_ignores_ambient_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit db_path is preserved verbatim and is never overridden by ambient DB env vars."""
    disposable_db = str(tmp_path / "explicit_leads.db")
    ambient_other = str(tmp_path / "ambient_other.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", ambient_other)
    monkeypatch.setenv("SALESAI_DB_PATH", ambient_other)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", ambient_other)

    with patch.object(LeadRegisterStore, "_init_db") as mock_init:
        store = LeadRegisterStore(db_path=disposable_db)
        assert store.db_path == disposable_db
        mock_init.assert_called_once()


def test_relative_path_semantics_preserved_without_silently_rewriting() -> None:
    """Explicit relative paths (including mocked operational path) are preserved verbatim without DB I/O."""
    for rel_path in ("relative_custom.db", "data/sales_outreach.db"):
        with (
            patch.object(LeadRegisterStore, "_init_db") as mock_init,
            patch("sales_engine.leads.register_store.sqlite3.connect") as mock_connect,
        ):
            store = LeadRegisterStore(db_path=rel_path)
            assert store.db_path == rel_path
            mock_init.assert_called_once()
            mock_connect.assert_not_called()


def test_pathlike_inputs_supported(tmp_path: Path) -> None:
    """pathlib.Path and custom os.PathLike[str] inputs are accepted and converted cleanly."""
    path_obj = tmp_path / "pathlib_leads.db"
    custom_obj = _CustomPathLike(str(tmp_path / "custom_pathlike.db"))

    with patch.object(LeadRegisterStore, "_init_db") as mock_init:
        s1 = LeadRegisterStore(path_obj)
        s2 = LeadRegisterStore(custom_obj)  # type: ignore[arg-type]
        assert s1.db_path == str(path_obj)
        assert s2.db_path == str(tmp_path / "custom_pathlike.db")
        assert mock_init.call_count == 2


def test_schema_initialization_succeeds_on_disposable_temp_db(tmp_path: Path) -> None:
    """Schema initialization succeeds against an isolated disposable temporary SQLite database."""
    disposable_db = tmp_path / "disposable_lead_register.db"
    assert not disposable_db.exists()

    store = LeadRegisterStore(db_path=disposable_db)
    assert disposable_db.exists()
    assert store.db_path == str(disposable_db)

    with sqlite3.connect(str(disposable_db)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lead_register_records';"
        ).fetchone()
        assert row is not None
        assert row[0] == "lead_register_records"
