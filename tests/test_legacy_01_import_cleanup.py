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
    """Sales-AI callers importing ai_engine.llm_client must not eagerly import RAG or scraping deps."""
    probe = _run_clean_import_probe(
        "import json, sys; "
        "import ai_engine.llm_client; "
        "import sales_engine.inbox.classifier; "
        "import sales_engine.inbox.suggestion; "
        "import sales_engine.outreach.email_generator; "
        "loaded = {m: (m in sys.modules) for m in ("
        "'chromadb', 'opentelemetry', 'scrapling', 'markdownify', "
        "'ai_engine.rag_retriever', 'ai_engine.data_ingestor')}; "
        "print(json.dumps(loaded))",
        tmp_path=tmp_path,
    )
    assert probe == {
        "chromadb": False,
        "opentelemetry": False,
        "scrapling": False,
        "markdownify": False,
        "ai_engine.rag_retriever": False,
        "ai_engine.data_ingestor": False,
    }


def test_ai_engine_package_preserves_lazy_public_api_and_attribute_error() -> None:
    """`ai_engine` preserves `__all__` and resolves exports lazily via `__getattr__`."""
    import ai_engine
    from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest, CodeGenerationResponse

    assert set(ai_engine.__all__) == {
        "BIMLLMClient",
        "CodeGenerationRequest",
        "CodeGenerationResponse",
        "BIMRAGRetriever",
        "BIMDataIngestor",
    }
    assert ai_engine.BIMLLMClient is BIMLLMClient
    assert ai_engine.CodeGenerationRequest is CodeGenerationRequest
    assert ai_engine.CodeGenerationResponse is CodeGenerationResponse
    assert "BIMLLMClient" in dir(ai_engine)
    assert "BIMRAGRetriever" in dir(ai_engine)

    with pytest.raises(AttributeError):
        getattr(ai_engine, "DOES_NOT_EXIST_LEGACY_SYMBOL")


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

    for removed in ("ScriptGenerationRequest", "ScriptGenerationResponse", "SalesLLMTestResponse"):
        assert removed not in imported_names

    # Active endpoints/schemas remain imported
    assert {"HealthResponse", "IngestRequest", "WebsiteAnalyzeRequest"}.issubset(imported_names)


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
