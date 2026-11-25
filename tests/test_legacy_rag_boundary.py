"""
tests/test_legacy_rag_boundary.py
Isolated regression tests for LEGACY-07: Legacy RAG and Ingestion Boundary Separation.

Verifies:
- Active Sales-AI modules (Search, Crawl, Gemini Analysis, Discovery, Outreach, Inbox)
  do not import legacy `ai_engine`, `chromadb`, or `langchain*`.
- Importing `backend.main` and `backend.sales_ui.router` does not eagerly load
  `ai_engine.rag_retriever`, `ai_engine.data_ingestor`, `ai_engine.ingest_queue`,
  `chromadb`, `ollama`, or start any ingestion worker.
- Legacy RAG and `/api/ingest*` routes lazily load dependencies only when invoked,
  preserve HTTP 503 failure semantics when optional dependencies are missing,
  preserve `HealthResponse` fields, and retain `Depends(verify_admin_token)` admin gates.

Zero operational SQLite access, zero real ChromaDB/embedding/scraping instantiation,
zero live provider or network calls.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.schemas import (
    ApprovalRequest,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    QueueItemResponse,
    RejectionRequest,
)
from sales_engine.llm_client import SalesLLMClient
from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent


class _DummyHTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _DummyStatus:
    HTTP_400_BAD_REQUEST = 400
    HTTP_404_NOT_FOUND = 404


def _run_probe(code: str, tmp_path: Path) -> dict[str, Any]:
    proc = run_isolated_subprocess(
        [sys.executable, "-B", "-c", code],
        isolated_db_path=tmp_path / "isolated_rag_boundary.db",
        trusted_dir=tmp_path,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(proc.stdout.strip())


def _load_backend_legacy_boundary_namespace(
    *,
    fake_app: Any | None = None,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Compile real legacy RAG and `/api/ingest*` handlers from `backend/main.py` AST."""
    main_path = REPO_ROOT / "backend" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))

    wanted_locks = {"_llm_client_lock", "_sales_llm_client_lock", "_rag_retriever_lock"}
    wanted_funcs = {
        "_ensure_ollama_dependency_available",
        "get_llm_client",
        "get_rag_retriever",
        "_get_legacy_ingest_queue",
        "_get_legacy_data_ingestor_cls",
        "health_check",
        "_run_approved_ingest",
        "submit_ingest_request",
        "list_knowledge_queue",
        "approve_knowledge_request",
        "reject_knowledge_request",
        "clear_queue_history",
        "get_submission_status",
    }

    selected: list[ast.stmt] = []
    found_locks: set[str] = set()
    found_funcs: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in wanted_locks
        ):
            selected.append(node)
            found_locks.add(node.targets[0].id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted_funcs:
            cloned = ast.copy_location(
                type(node)(
                    name=node.name,
                    args=node.args,
                    body=node.body,
                    decorator_list=[],
                    returns=node.returns,
                    type_comment=node.type_comment,
                ),
                node,
            )
            selected.append(cloned)
            found_funcs.add(node.name)

    assert found_locks == wanted_locks
    assert found_funcs == wanted_funcs

    app_obj = fake_app if fake_app is not None else SimpleNamespace(state=SimpleNamespace())
    if llm_client is not None:
        app_obj.state.llm_client = llm_client

    ns: dict[str, Any] = {
        "__file__": str(main_path),
        "app": app_obj,
        "Path": Path,
        "Optional": Optional,
        "List": List,
        "threading": threading,
        "HTTPException": _DummyHTTPException,
        "status": _DummyStatus,
        "Depends": lambda dep: dep,
        "BackgroundTasks": object,
        "verify_admin_token": lambda: {"sub": "admin", "role": "admin"},
        "SalesLLMClient": SalesLLMClient,
        "SalesLLMRequest": object,
        "HealthResponse": HealthResponse,
        "IngestRequest": IngestRequest,
        "IngestResponse": IngestResponse,
        "QueueItemResponse": QueueItemResponse,
        "ApprovalRequest": ApprovalRequest,
        "RejectionRequest": RejectionRequest,
    }
    mod = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(mod)
    exec(compile(mod, filename=str(main_path), mode="exec"), ns)
    return ns


def test_active_sales_imports_exclude_legacy_rag_and_ingestion(tmp_path: Path) -> None:
    """Ordinary Sales-AI imports (Search, Crawl, Gemini, Discovery, Outreach, Inbox) stay free of legacy RAG."""
    probe = _run_probe(
        "import json, sys; "
        "import sales_engine.discovery.brave_search; "
        "import sales_engine.discovery.discovery_orchestrator; "
        "import sales_engine.research.website_crawler; "
        "import sales_engine.research.company_analysis_service; "
        "import sales_engine.analysis.website_analyzer; "
        "import sales_engine.outreach.email_generator; "
        "import sales_engine.outreach.outreach_context_builder; "
        "import sales_engine.outreach.email_prompt_builder; "
        "import sales_engine.inbox.classifier; "
        "import sales_engine.inbox.suggestion; "
        "import sales_engine.web.website_fetcher; "
        "prohibited = ('ai_engine', 'ai_engine.rag_retriever', 'ai_engine.data_ingestor', "
        "'ai_engine.ingest_queue', 'chromadb', 'opentelemetry', "
        "'langchain_core', 'langchain_ollama', 'langchain_chroma', 'ollama'); "
        "print(json.dumps({m: (m in sys.modules) for m in prohibited}))",
        tmp_path=tmp_path,
    )
    assert all(loaded is False for loaded in probe.values()), f"Unexpected legacy modules loaded: {probe}"


def test_backend_main_and_sales_ui_import_without_legacy_rag_or_queue_init(tmp_path: Path) -> None:
    """Importing `backend.main` and `backend.sales_ui.router` must not eagerly load legacy RAG/ingestion."""
    probe = _run_probe(
        "import json, sys; "
        "import backend.main; "
        "import backend.sales_ui.router; "
        "prohibited = ('ai_engine', 'ai_engine.rag_retriever', 'ai_engine.data_ingestor', "
        "'ai_engine.ingest_queue', 'chromadb', 'opentelemetry', "
        "'langchain_core', 'langchain_ollama', 'langchain_chroma', 'ollama'); "
        "loaded = {m: (m in sys.modules) for m in prohibited}; "
        "routes = sorted({getattr(r, 'path', '') for r in backend.main.app.routes}); "
        "rag_initialized = getattr(backend.main.app.state, 'rag_retriever', None) is not None; "
        "print(json.dumps({'loaded': loaded, 'rag_initialized': rag_initialized, 'routes': routes}))",
        tmp_path=tmp_path,
    )
    assert all(v is False for v in probe["loaded"].values()), f"Eager legacy import detected: {probe['loaded']}"
    assert probe["rag_initialized"] is False
    expected_routes = {
        "/health",
        "/api/ingest",
        "/api/ingest/queue",
        "/api/ingest/approve",
        "/api/ingest/reject",
        "/api/ingest/queue/clear-history",
        "/api/ingest/status/{request_id}",
    }
    assert expected_routes.issubset(set(probe["routes"]))


def test_static_source_boundaries_settings_and_admin_auth_preserved() -> None:
    """Verify static import boundaries across Sales-AI, backend/main.py, Settings UI, and admin gates."""
    # 1. Protected active Sales-AI modules never import legacy ai_engine, chromadb, or langchain
    protected_sales_files = (
        "sales_engine/discovery/brave_search.py",
        "sales_engine/discovery/discovery_orchestrator.py",
        "sales_engine/research/website_crawler.py",
        "sales_engine/research/company_analysis_service.py",
        "sales_engine/research/company_research_service.py",
        "sales_engine/web/website_fetcher.py",
        "sales_engine/analysis/website_analyzer.py",
        "sales_engine/outreach/email_generator.py",
        "sales_engine/outreach/outreach_context_builder.py",
        "sales_engine/outreach/email_prompt_builder.py",
        "sales_engine/inbox/classifier.py",
        "sales_engine/inbox/suggestion.py",
    )
    forbidden_prefixes = ("ai_engine", "chromadb", "langchain")
    for rel_path in protected_sales_files:
        tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"), filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), f"Forbidden import {alias.name} in {rel_path}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), f"Forbidden import {node.module} in {rel_path}"

    # 2. backend/main.py has zero top-level legacy imports, no dead imports, and preserves admin Depends + clean lifespan
    main_src = (REPO_ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    main_tree = ast.parse(main_src, filename="backend/main.py")

    def _iter_top_level_imports(stmts: list[ast.stmt]):
        for stmt in stmts:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                yield stmt
            elif isinstance(stmt, ast.Try):
                yield from _iter_top_level_imports(stmt.body)
                for h in stmt.handlers:
                    yield from _iter_top_level_imports(h.body)

    top_modules: set[str] = set()
    top_names: set[str] = set()
    for imp in _iter_top_level_imports(main_tree.body):
        if isinstance(imp, ast.Import):
            for a in imp.names:
                top_modules.add(a.name)
                top_names.add(a.name)
        elif isinstance(imp, ast.ImportFrom):
            if imp.module:
                top_modules.add(imp.module)
            for a in imp.names:
                top_names.add(a.name)

    for mod in top_modules:
        assert not mod.startswith(("ai_engine", "chromadb", "langchain")), f"Eager top-level legacy import: {mod}"
    for removed_dead in ("re", "ast", "time", "FileResponse"):
        assert removed_dead not in top_names, f"Dead legacy import still present: {removed_dead}"

    funcs_by_name = {
        node.name: node
        for node in main_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    # Lifespan must not touch RAG or ingestion workers
    lifespan_names = {n.id for n in ast.walk(funcs_by_name["lifespan"]) if isinstance(n, ast.Name)}
    for forbidden in ("BIMRAGRetriever", "BIMDataIngestor", "ingest_queue", "_run_approved_ingest", "get_rag_retriever"):
        assert forbidden not in lifespan_names

    # All 4 admin ingest routes retain Depends(verify_admin_token)
    for admin_fn_name in (
        "list_knowledge_queue",
        "approve_knowledge_request",
        "reject_knowledge_request",
        "clear_queue_history",
    ):
        fn = funcs_by_name[admin_fn_name]
        default_sources = [ast.unparse(d) for d in fn.args.defaults]
        assert any("Depends(verify_admin_token)" in s for s in default_sources), (
            f"Admin dependency missing on {admin_fn_name}: {default_sources}"
        )

    # 3. backend/sales_ui/router.py and settings.html no longer import chromadb or render legacy ChromaDB badge
    router_src = (REPO_ROOT / "backend" / "sales_ui" / "router.py").read_text(encoding="utf-8")
    router_tree = ast.parse(router_src, filename="backend/sales_ui/router.py")
    for node in ast.walk(router_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(("chromadb", "ai_engine"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(("chromadb", "ai_engine"))
    assert "chroma_operational" not in router_src

    settings_html = (REPO_ROOT / "backend" / "sales_ui" / "templates" / "settings.html").read_text(encoding="utf-8")
    assert "chroma_operational" not in settings_html
    assert "ChromaDB" not in settings_html
    assert "ollama_reachable" in settings_html
    assert "research_storage_available" in settings_html


def test_rag_retriever_lazy_boundary_concurrency_and_missing_dep_503() -> None:
    """`get_rag_retriever` lazily initializes once under lock and returns HTTP 503 if ChromaDB is unavailable."""
    sync_ok = MagicMock()
    sync_ok.list.return_value = SimpleNamespace(models=[SimpleNamespace(model="qwen2.5:1.5b")])
    llm = SalesLLMClient(default_model="qwen2.5-coder:1.5b", sync_client=sync_ok)
    fake_app = SimpleNamespace(state=SimpleNamespace())
    ns = _load_backend_legacy_boundary_namespace(fake_app=fake_app, llm_client=llm)

    # 1. Missing `ai_engine.rag_retriever` -> HTTP 503 both directly and via health_check
    with patch.dict(sys.modules, {"ai_engine.rag_retriever": None}):
        with pytest.raises(_DummyHTTPException) as exc_direct:
            ns["get_rag_retriever"]()
        assert exc_direct.value.status_code == 503
        assert "ChromaDB" in exc_direct.value.detail

        with pytest.raises(_DummyHTTPException) as exc_health:
            ns["health_check"]()
        assert exc_health.value.status_code == 503
        assert "ChromaDB" in exc_health.value.detail

    # 2. Thread-safe lazy initialization with mocked BIMRAGRetriever (zero real ChromaDB touched)
    init_calls: list[dict[str, Any]] = []

    class FakeBIMRAGRetriever:
        def __init__(self, *, persist_dir: str, collection_name: str, embedding_model: str):
            init_calls.append(
                {
                    "persist_dir": persist_dir,
                    "collection_name": collection_name,
                    "embedding_model": embedding_model,
                }
            )

        def count(self) -> int:
            return 17

    fake_rag_mod = ModuleType("ai_engine.rag_retriever")
    fake_rag_mod.BIMRAGRetriever = FakeBIMRAGRetriever  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"ai_engine.rag_retriever": fake_rag_mod}):
        barrier = threading.Barrier(4)
        results: list[Any] = []

        def worker() -> None:
            barrier.wait(timeout=5.0)
            results.append(ns["get_rag_retriever"]())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()

        assert len(init_calls) == 1
        assert init_calls[0]["collection_name"] == "bim_rules"
        assert init_calls[0]["embedding_model"] == "nomic-embed-text"
        assert len(results) == 4 and all(r is results[0] for r in results)

        # 3. health_check preserves HealthResponse fields & semantics
        health = ns["health_check"]()
        assert isinstance(health, HealthResponse)
        assert health.status == "healthy"
        assert health.ollama_connected is True
        assert health.chromadb_connected is True
        assert health.indexed_rules_count == 17
        assert health.available_models == ["qwen2.5:1.5b"]


def test_legacy_ingest_routes_lazy_boundary_503_and_contract_preservation() -> None:
    """All `/api/ingest*` routes preserve contracts with mocked queue/ingestor and raise 503 when unavailable."""
    ns = _load_backend_legacy_boundary_namespace()

    # 1. Missing legacy modules -> HTTP 503 instead of AttributeError
    with patch.dict(sys.modules, {"ai_engine.ingest_queue": None, "ai_engine.data_ingestor": None}):
        with pytest.raises(_DummyHTTPException) as exc_q:
            asyncio.run(ns["list_knowledge_queue"](status_filter=None, admin_user={"sub": "admin"}))
        assert exc_q.value.status_code == 503

        with pytest.raises(_DummyHTTPException) as exc_sub:
            asyncio.run(ns["submit_ingest_request"](IngestRequest(url="https://docs.example.com/revit")))
        assert exc_sub.value.status_code == 503

    # 2. Mocked legacy queue & ingestor preserve full route contracts without touching disk or ChromaDB
    queue_store: dict[str, dict[str, Any]] = {}

    class FakeQueue:
        def enqueue(self, *, url: str, slug: Optional[str], submitter: str) -> dict[str, Any]:
            item = {
                "request_id": "req_mock0001",
                "url": url,
                "slug": slug,
                "status": "pending",
                "submitter": submitter,
                "submitted_at": "2026-11-25T12:00:00Z",
                "processed_at": None,
                "message": "Awaiting administrator approval before indexing into ChromaDB.",
            }
            queue_store[item["request_id"]] = item
            return item

        def list_items(self, status_filter: Optional[str] = None) -> list[dict[str, Any]]:
            items = list(queue_store.values())
            return [i for i in items if status_filter is None or i["status"] == status_filter]

        def get(self, request_id: str) -> Optional[dict[str, Any]]:
            return queue_store.get(request_id)

        def update_status(
            self,
            request_id: str,
            status: str,
            message: Optional[str] = None,
            slug: Optional[str] = None,
        ) -> Optional[dict[str, Any]]:
            item = queue_store.get(request_id)
            if not item:
                return None
            item["status"] = status
            if message is not None:
                item["message"] = message
            if slug is not None:
                item["slug"] = slug
            return item

        def clear_processed(self) -> int:
            before = len(queue_store)
            for rid in [k for k, v in queue_store.items() if v["status"] in ("approved", "rejected")]:
                del queue_store[rid]
            return before - len(queue_store)

    class FakeIngestor:
        @classmethod
        def generate_smart_slug(cls, url: str, user_slug: Optional[str] = None):
            return ("revit_api_sample", "Normalized slug.") if user_slug else ("revit_api_sample", None)

        def ingest_url(self, url: str, slug: Optional[str] = None) -> dict[str, Any]:
            return {
                "indexed_chunks": 3,
                "file_path": "data/rules/revit_api_sample.md",
                "effective_slug": slug or "revit_api_sample",
                "slug_feedback": None,
            }

    fake_queue_mod = ModuleType("ai_engine.ingest_queue")
    fake_queue_mod.ingest_queue = FakeQueue()  # type: ignore[attr-defined]
    fake_ingestor_mod = ModuleType("ai_engine.data_ingestor")
    fake_ingestor_mod.BIMDataIngestor = FakeIngestor  # type: ignore[attr-defined]

    with patch.dict(
        sys.modules,
        {"ai_engine.ingest_queue": fake_queue_mod, "ai_engine.data_ingestor": fake_ingestor_mod},
    ):
        # Invalid URL scheme -> HTTP 400
        with pytest.raises(_DummyHTTPException) as exc_bad_url:
            asyncio.run(ns["submit_ingest_request"](IngestRequest(url="ftp://invalid.example.com/doc")))
        assert exc_bad_url.value.status_code == 400

        # Submit -> IngestResponse(status="pending")
        sub = asyncio.run(
            ns["submit_ingest_request"](
                IngestRequest(url="https://docs.example.com/revit", slug="Sample", submitter="QA")
            )
        )
        assert isinstance(sub, IngestResponse)
        assert sub.status == "pending"
        assert sub.current_state == "pending"
        assert sub.request_id == "req_mock0001"
        assert "Normalized slug." in sub.message

        # List queue & public status
        listed = asyncio.run(ns["list_knowledge_queue"](status_filter="pending", admin_user={"sub": "admin"}))
        assert len(listed) == 1 and isinstance(listed[0], QueueItemResponse)
        tracked = asyncio.run(ns["get_submission_status"]("req_mock0001"))
        assert isinstance(tracked, QueueItemResponse) and tracked.slug == "revit_api_sample"

        # Unknown tracking ID -> HTTP 404
        with pytest.raises(_DummyHTTPException) as exc_404:
            asyncio.run(ns["get_submission_status"]("req_missing"))
        assert exc_404.value.status_code == 404

        # Approve -> schedules background task without starting worker automatically on import
        scheduled: list[tuple[Any, tuple[Any, ...]]] = []
        bg = SimpleNamespace(add_task=lambda fn, *args: scheduled.append((fn, args)))
        approved = asyncio.run(
            ns["approve_knowledge_request"](
                ApprovalRequest(request_id="req_mock0001"),
                bg,
                admin_user={"sub": "admin"},
            )
        )
        assert approved["status"] == "processing"
        assert len(scheduled) == 1
        assert scheduled[0][0] is ns["_run_approved_ingest"]
        assert scheduled[0][1] == ("req_mock0001", "https://docs.example.com/revit", "revit_api_sample")

        # Execute scheduled worker with mocked ingestor -> transitions to approved
        ns["_run_approved_ingest"](*scheduled[0][1])
        assert queue_store["req_mock0001"]["status"] == "approved"

        # Already-approved idempotency
        approved_again = asyncio.run(
            ns["approve_knowledge_request"](
                ApprovalRequest(request_id="req_mock0001"),
                bg,
                admin_user={"sub": "admin"},
            )
        )
        assert approved_again["status"] == "approved"

        # Reject and clear-history contracts
        rejected = asyncio.run(
            ns["reject_knowledge_request"](
                RejectionRequest(request_id="req_mock0001", reason="Not compliant"),
                admin_user={"sub": "admin"},
            )
        )
        assert rejected["status"] == "rejected"
        cleared = asyncio.run(ns["clear_queue_history"](admin_user={"sub": "admin"}))
        assert cleared["status"] == "success" and cleared["removed_count"] == 1
