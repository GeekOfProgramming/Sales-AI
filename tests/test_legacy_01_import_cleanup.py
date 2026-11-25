"""
tests/test_legacy_01_import_cleanup.py
Isolated regression tests for LEGACY-01 import-boundary and dead pyBIM-LLM import cleanup.
Zero operational DB access, zero FastAPI server startup, zero live network/LLM calls.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

import pytest

from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_clean_import_probe(code: str, tmp_path: Path) -> dict[str, object]:
    """Execute an import probe in a DB-isolated Python subprocess with repo root on sys.path."""
    proc = run_isolated_subprocess(
        [sys.executable, "-B", "-c", code],
        isolated_db_path=tmp_path / "isolated_import_probe.db",
        trusted_dir=tmp_path,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(proc.stdout.strip())


def test_ai_engine_submodule_imports_do_not_load_chromadb_or_scrapling(tmp_path: Path) -> None:
    """Sales-AI callers must not load retired `ai_engine` modules, ChromaDB, or scraping dependencies."""
    probe = _run_clean_import_probe(
        "import importlib.util, json, sys\n"
        "def has_spec(m):\n"
        "    try:\n"
        "        return importlib.util.find_spec(m) is not None\n"
        "    except ModuleNotFoundError:\n"
        "        return False\n"
        "import sales_engine.inbox.classifier\n"
        "import sales_engine.inbox.suggestion\n"
        "import sales_engine.outreach.email_generator\n"
        "loaded = {m: (m in sys.modules) for m in ("
        "'ai_engine', 'chromadb', 'opentelemetry', 'scrapling', 'markdownify')}\n"
        "retired_specs = {m: has_spec(m) for m in ("
        "'ai_engine.llm_client', 'ai_engine.rag_retriever', "
        "'ai_engine.data_ingestor', 'ai_engine.ingest_queue')}\n"
        "print(json.dumps({'loaded': loaded, 'retired_specs': retired_specs}))",
        tmp_path=tmp_path,
    )
    assert probe["loaded"] == {
        "ai_engine": False,
        "chromadb": False,
        "opentelemetry": False,
        "scrapling": False,
        "markdownify": False,
    }
    assert probe["retired_specs"] == {
        "ai_engine.llm_client": False,
        "ai_engine.rag_retriever": False,
        "ai_engine.data_ingestor": False,
        "ai_engine.ingest_queue": False,
    }


def test_ai_engine_package_completely_retired() -> None:
    """Verify legacy `ai_engine` tracked implementation files and symbols are retired cleanly."""
    import importlib
    import importlib.util

    # Tolerate residual empty or __pycache__ directories (PEP 420 namespace case),
    # but assert zero legacy Python source files remain in ai_engine/.
    ai_dir = REPO_ROOT / "ai_engine"
    if ai_dir.exists():
        assert list(ai_dir.rglob("*.py")) == []

    for mod_name in (
        "ai_engine.llm_client",
        "ai_engine.rag_retriever",
        "ai_engine.data_ingestor",
        "ai_engine.ingest_queue",
    ):
        try:
            spec = importlib.util.find_spec(mod_name)
        except ModuleNotFoundError:
            spec = None
        assert spec is None

    # Even if an empty residual directory acts as a PEP 420 namespace package,
    # none of the retired legacy symbols are importable from `ai_engine`.
    try:
        ns_pkg = importlib.import_module("ai_engine")
    except ModuleNotFoundError:
        ns_pkg = None

    if ns_pkg is not None:
        assert getattr(ns_pkg, "__file__", None) is None
        for sym in (
            "BIMLLMClient",
            "CodeGenerationRequest",
            "CodeGenerationResponse",
            "BIMRAGRetriever",
            "BIMDataIngestor",
            "ingest_queue",
        ):
            assert not hasattr(ns_pkg, sym)


def test_backend_package_preserves_lazy_schema_exports_without_eager_load(tmp_path: Path) -> None:
    """Importing `backend.db_path` must not eagerly import `backend.schemas` or `pydantic`."""
    probe = _run_clean_import_probe(
        "import json, sys; "
        "import backend.db_path; "
        "before = {'pydantic': 'pydantic' in sys.modules, 'schemas': 'backend.schemas' in sys.modules}; "
        "import backend; "
        "after_pkg = {'pydantic': 'pydantic' in sys.modules, 'schemas': 'backend.schemas' in sys.modules}; "
        "from backend import HealthResponse, ScriptGenerationRequest; "
        "after_attr = {'pydantic': 'pydantic' in sys.modules, 'schemas': 'backend.schemas' in sys.modules, "
        "'ok': HealthResponse.__name__ == 'HealthResponse' and ScriptGenerationRequest.__name__ == 'ScriptGenerationRequest'}; "
        "print(json.dumps({'before': before, 'after_pkg': after_pkg, 'after_attr': after_attr}))",
        tmp_path=tmp_path,
    )
    assert probe["before"] == {"pydantic": False, "schemas": False}
    assert probe["after_pkg"] == {"pydantic": False, "schemas": False}
    assert probe["after_attr"] == {"pydantic": True, "schemas": True, "ok": True}

    import backend

    assert set(backend.__all__) == {
        "ElementMetadata",
        "ScriptGenerationRequest",
        "ScriptGenerationResponse",
        "HealthResponse",
    }
    assert "HealthResponse" in dir(backend)
    with pytest.raises(AttributeError):
        getattr(backend, "NONEXISTENT_BACKEND_EXPORT")


def test_backend_main_removed_dead_legacy_schema_imports_and_parses_cleanly() -> None:
    """Verify unused legacy schema imports were removed from `backend/main.py` without syntax regressions."""
    main_path = REPO_ROOT / "backend" / "main.py"
    source = main_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(main_path))

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "backend.schemas":
            imported_names.update(alias.name for alias in node.names)

    for removed in (
        "ScriptGenerationRequest",
        "ScriptGenerationResponse",
        "SalesLLMTestResponse",
        "IngestRequest",
        "IngestResponse",
        "QueueItemResponse",
        "ApprovalRequest",
        "RejectionRequest",
    ):
        assert removed not in imported_names

    # Active endpoints/schemas remain imported
    assert {"HealthResponse", "LoginRequest", "TokenResponse", "WebsiteAnalyzeRequest"}.issubset(imported_names)


def test_deterministic_inbox_callers_operate_without_chromadb_or_llm_calls() -> None:
    """Direct Sales-AI inbox callers operate deterministically after lazy ai_engine refactor."""
    from sales_engine.inbox.classifier import ReplyClassifier
    from sales_engine.inbox.models import Conversation, InboundMessage
    from sales_engine.inbox.suggestion import ReplySuggestionEngine

    classifier = ReplyClassifier()
    result = classifier.classify(
        from_email="lead@example.com",
        subject="Re: BIM Coordination",
        body_text="Yes, we are interested — happy to discuss.",
    )
    assert result.category == "positive_interest"
    assert result.method == "deterministic"

    conv = Conversation(conversation_id="conv_test", candidate_id="cand_test", contact_email="lead@example.com")
    inbound = InboundMessage(
        inbound_message_id="inb_test",
        provider_message_id="prov_msg_test",
        conversation_id="conv_test",
        from_email="lead@example.com",
        to_emails=["team@pybim.it"],
        subject="Re: BIM Coordination",
        body_text="Yes, let's talk next week.",
        classification="positive_interest",
        received_at="2026-11-24T12:00:00Z",
    )
    engine = ReplySuggestionEngine()
    suggestion = engine.generate_suggestion(conversation=conv, inbound_message=inbound, contact_name="Marco")
    assert suggestion is not None
    assert suggestion.status == "pending_review"
    assert suggestion.generation_model == "deterministic_rules"
