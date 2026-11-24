"""
tests/test_operational_db_path_contract.py
Isolated operational database path-contract tests for BackupEngine (P-1),
MailboxRepository (P-2), ReviewStore (P-4), MailboxPilotRunner, and Outbound CLI.
Zero operational SQLite connections, zero SMTP/IMAP access, zero Scheduled Task queries.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sales_engine.candidates.backup_engine import REPO_ROOT, resolve_configured_db_path
from sales_engine.db_resolver import DatabaseResolutionError
from sales_engine.inbox.mailbox_repository import MailboxRepository, get_default_inbox_db_path
from sales_engine.sending.review_store import ReviewStore, get_default_db_path


def test_backup_engine_p1_precedence_and_repo_root_anchoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BackupEngine preserves P-1 precedence, repo-root anchoring, and Path return type."""
    p_out = tmp_path / "outreach.db"
    p_db = tmp_path / "salesai_db.db"
    p_cand = tmp_path / "candidates.db"
    other_cwd = tmp_path / "outside_repo"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    # 1. All unset -> (REPO_ROOT / "data/sales_outreach.db").resolve() regardless of cwd
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.delenv("SALESAI_DB_PATH", raising=False)
    monkeypatch.delenv("SALESAI_CANDIDATES_DB", raising=False)
    res_default = resolve_configured_db_path()
    assert isinstance(res_default, Path)
    assert res_default == (REPO_ROOT / "data" / "sales_outreach.db").resolve()

    # 2. Precedence: SALES_OUTREACH_DB > SALESAI_DB_PATH > SALESAI_CANDIDATES_DB
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(p_cand))
    assert resolve_configured_db_path() == p_cand.resolve()

    monkeypatch.setenv("SALESAI_DB_PATH", str(p_db))
    assert resolve_configured_db_path() == p_db.resolve()

    monkeypatch.setenv("SALES_OUTREACH_DB", str(p_out))
    assert resolve_configured_db_path() == p_out.resolve()

    # 3. Valid high-priority ignores malformed lower-priority env vars
    monkeypatch.setenv("SALESAI_DB_PATH", "file:ignored.db?mode=ro")
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", "   ")
    assert resolve_configured_db_path() == p_out.resolve()

    # 4. Relative configured path anchors to REPO_ROOT even when cwd is outside repo
    monkeypatch.setenv("SALES_OUTREACH_DB", "data/custom_backup_target.db")
    assert resolve_configured_db_path() == (REPO_ROOT / "data" / "custom_backup_target.db").resolve()

    # 5. Empty string skips to next var; selected whitespace-only fails closed
    monkeypatch.setenv("SALES_OUTREACH_DB", "")
    monkeypatch.setenv("SALESAI_DB_PATH", str(p_db))
    assert resolve_configured_db_path() == p_db.resolve()

    monkeypatch.setenv("SALES_OUTREACH_DB", "   ")
    with pytest.raises(DatabaseResolutionError):
        resolve_configured_db_path()


def test_mailbox_repository_p2_precedence_and_no_sqlite_on_invalid_config(
    tmp_path: Path,
) -> None:
    """MailboxRepository preserves P-2 (ignores SALESAI_CANDIDATES_DB), relative Paths, and rejects whitespace."""
    p_out = str(tmp_path / "mailbox_out.db")
    p_db = str(tmp_path / "mailbox_db.db")
    p_cand = str(tmp_path / "mailbox_cand.db")

    assert get_default_inbox_db_path(env={}) == Path("data/sales_outreach.db")
    assert get_default_inbox_db_path(env={"SALESAI_CANDIDATES_DB": p_cand}) == Path("data/sales_outreach.db")
    assert get_default_inbox_db_path(env={"SALESAI_DB_PATH": p_db}) == Path(p_db)
    assert get_default_inbox_db_path(env={"SALES_OUTREACH_DB": p_out, "SALESAI_DB_PATH": p_db}) == Path(p_out)
    # Historical empty-string fall-through preserved; lower-priority whitespace ignored when higher valid
    assert get_default_inbox_db_path(env={"SALES_OUTREACH_DB": "", "SALESAI_DB_PATH": p_db}) == Path(p_db)
    assert get_default_inbox_db_path(env={"SALES_OUTREACH_DB": p_out, "SALESAI_DB_PATH": "   "}) == Path(p_out)
    # Relative path preserved as Path
    assert get_default_inbox_db_path(env={"SALES_OUTREACH_DB": "rel/inbox.db"}) == Path("rel/inbox.db")

    # Selected whitespace-only fails closed before mkdir or SQLite
    with pytest.raises(DatabaseResolutionError):
        get_default_inbox_db_path(env={"SALES_OUTREACH_DB": "   "})

    with (
        patch("sales_engine.inbox.mailbox_repository.Path.mkdir") as mock_mkdir,
        patch("sales_engine.inbox.mailbox_repository.sqlite3.connect") as mock_sql,
    ):
        with pytest.raises(DatabaseResolutionError):
            MailboxRepository(db_path="   ")
        mock_mkdir.assert_not_called()
        mock_sql.assert_not_called()

        # Explicit path preserved without touching SQLite (auto_migrate=False)
        repo = MailboxRepository(db_path=p_out, auto_migrate=False)
        assert repo.db_path == Path(p_out)
        mock_sql.assert_not_called()


def test_review_store_p4_precedence_and_no_sqlite_on_invalid_config(
    tmp_path: Path,
) -> None:
    """ReviewStore preserves P-4 (SALES_OUTREACH_DB only), relative Paths, and rejects whitespace."""
    p_out = str(tmp_path / "review_out.db")
    p_db = str(tmp_path / "review_db.db")
    p_cand = str(tmp_path / "review_cand.db")

    assert get_default_db_path(env={}) == Path("data/sales_outreach.db")
    assert get_default_db_path(env={"SALESAI_DB_PATH": p_db, "SALESAI_CANDIDATES_DB": p_cand}) == Path("data/sales_outreach.db")
    assert get_default_db_path(env={"SALES_OUTREACH_DB": p_out}) == Path(p_out)
    assert get_default_db_path(env={"SALES_OUTREACH_DB": ""}) == Path("data/sales_outreach.db")
    assert get_default_db_path(env={"SALES_OUTREACH_DB": "rel/review.db"}) == Path("rel/review.db")

    with pytest.raises(DatabaseResolutionError):
        get_default_db_path(env={"SALES_OUTREACH_DB": "   "})

    with (
        patch("sales_engine.sending.review_store.Path.mkdir") as mock_mkdir,
        patch.object(ReviewStore, "_init_db") as mock_init,
        patch("sales_engine.sending.review_store.sqlite3.connect") as mock_sql,
    ):
        with pytest.raises(DatabaseResolutionError):
            ReviewStore(db_path="   ")
        mock_mkdir.assert_not_called()
        mock_init.assert_not_called()
        mock_sql.assert_not_called()

        store = ReviewStore(db_path=p_out)
        assert store.db_path == Path(p_out)
        mock_init.assert_called_once()
        mock_sql.assert_not_called()


def test_mailbox_pilot_runner_propagates_resolved_path_without_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MailboxPilotRunner uses BackupEngine P-1 resolution when db_path omitted and propagates to MailboxRepository."""
    from scripts import run_mailbox_pilot

    p_out = tmp_path / "pilot_outreach.db"
    p_explicit = str(tmp_path / "pilot_explicit.db")

    monkeypatch.setenv("SALES_OUTREACH_DB", str(p_out))
    monkeypatch.delenv("SALESAI_DB_PATH", raising=False)
    monkeypatch.delenv("SALESAI_CANDIDATES_DB", raising=False)

    with (
        patch.object(run_mailbox_pilot, "MailboxRepository") as mock_repo_cls,
        patch.object(run_mailbox_pilot, "MailboxCredentialStore"),
    ):
        # Scheduled-task style (no --db-path) -> resolves via resolve_configured_db_path()
        runner_default = run_mailbox_pilot.MailboxPilotRunner(db_path=None)
        assert runner_default.db_path == str(p_out.resolve())
        mock_repo_cls.assert_called_once_with(db_path=str(p_out.resolve()))

    with (
        patch.object(run_mailbox_pilot, "MailboxRepository") as mock_repo_cls,
        patch.object(run_mailbox_pilot, "MailboxCredentialStore"),
    ):
        # Explicit --db-path overrides ambient env
        runner_explicit = run_mailbox_pilot.MailboxPilotRunner(db_path=p_explicit)
        assert runner_explicit.db_path == p_explicit
        mock_repo_cls.assert_called_once_with(db_path=p_explicit)

        # Whitespace explicit db_path fails closed before MailboxRepository
        with pytest.raises(DatabaseResolutionError):
            run_mailbox_pilot.MailboxPilotRunner(db_path="   ")


def test_outbound_pilot_p4_resolution_and_downstream_propagation(
    tmp_path: Path,
) -> None:
    """run_outbound_pilot preserves P-4 contract, fails closed on empty/whitespace, and propagates to stores."""
    from scripts import run_outbound_pilot

    p_out = str(tmp_path / "outbound_out.db")
    p_explicit = str(tmp_path / "outbound_explicit.db")

    # P-4 precedence (ignores SALESAI_DB_PATH and SALESAI_CANDIDATES_DB)
    assert run_outbound_pilot.resolve_outbound_db_path(env={}) == "data/sales_outreach.db"
    assert (
        run_outbound_pilot.resolve_outbound_db_path(
            env={"SALESAI_DB_PATH": p_out, "SALESAI_CANDIDATES_DB": p_out}
        )
        == "data/sales_outreach.db"
    )
    assert (
        run_outbound_pilot.resolve_outbound_db_path(env={"SALES_OUTREACH_DB": p_out})
        == p_out
    )
    # Explicit overrides ambient env (including invalid ambient env)
    assert (
        run_outbound_pilot.resolve_outbound_db_path(
            p_explicit, env={"SALES_OUTREACH_DB": "   "}
        )
        == p_explicit
    )
    # Empty or whitespace selected env / explicit path fails closed
    for bad in ("", "   "):
        with pytest.raises(DatabaseResolutionError):
            run_outbound_pilot.resolve_outbound_db_path(bad)
        with pytest.raises(DatabaseResolutionError):
            run_outbound_pilot.resolve_outbound_db_path(env={"SALES_OUTREACH_DB": bad})

    # Verify check_draft_eligibility passes abs_db consistently to MailboxRepository, Safeguards, OutboundQueueStore
    fake_repo = MagicMock()
    fake_repo.get_draft.return_value = None
    with (
        patch.object(run_outbound_pilot, "MailboxRepository", return_value=fake_repo) as mock_repo,
        patch.object(run_outbound_pilot, "OutboundSafeguardService") as mock_safe,
        patch.object(run_outbound_pilot, "OutboundQueueStore") as mock_qstore,
    ):
        res = run_outbound_pilot.check_draft_eligibility(db_path=p_explicit, draft_id="drf_test")
        expected_abs = os.path.abspath(p_explicit)
        assert res["eligible"] is False
        mock_repo.assert_called_once_with(db_path=expected_abs, auto_migrate=False)
        mock_safe.assert_called_once_with(db_path=expected_abs)
        mock_qstore.assert_called_once_with(db_path=expected_abs, auto_apply_schema=False)
