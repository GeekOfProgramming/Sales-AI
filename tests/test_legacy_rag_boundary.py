"""
tests/test_legacy_rag_boundary.py
Isolated regression tests for LEGACY-08C1: Legacy Ingestion API Retirement and
Health Decoupling from BIM RAG.

Verifies:
- Active Sales-AI modules (Search, Crawl, Gemini Analysis, Discovery, Outreach, Inbox)
  do not import legacy `ai_engine`, `chromadb`, or `langchain*`.
- Importing `backend.main` and `backend.sales_ui.router` loads zero legacy RAG/ingestion
  modules and exposes zero retired `/api/ingest*` routes while preserving active routes.
- `operator_security_middleware` no longer exempts `/api/ingest*` while preserving
  `/health`, `/sales/login`, `/sales/logout`, `/static`, and `/api/auth/login`.
- `GET /health` operates cleanly with local Ollama only (zero RAG/ChromaDB dependency),
  preserves HTTP 503 when `ollama` is unavailable, and omits retired ChromaDB fields.

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
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.schemas import HealthResponse
from sales_engine.llm_client import SalesLLMClient
from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent

RETIRED_INGEST_ROUTES = {
    "/api/ingest",
    "/api/ingest/queue",
    "/api/ingest/approve",
    "/api/ingest/reject",
    "/api/ingest/queue/clear-history",
    "/api/ingest/status/{request_id}",
}


class _DummyHTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _DummyStatus:
    HTTP_303_SEE_OTHER = 303
    HTTP_401_UNAUTHORIZED = 401


class _DummyResponse:
    def __init__(self, status_code: int = 200, content: Any = None, url: str | None = None, headers: Any = None):
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = headers or {}


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


def _load_backend_functions(
    wanted_funcs: set[str],
    *,
    fake_app: Any | None = None,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Compile selected real functions from `backend/main.py` AST without starting FastAPI."""
    main_path = REPO_ROOT / "backend" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))

    wanted_locks = {"_llm_client_lock", "_sales_llm_client_lock"}
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
        "threading": threading,
        "Request": object,
        "HTTPException": _DummyHTTPException,
        "status": _DummyStatus,
        "RedirectResponse": lambda url, status_code=303: _DummyResponse(status_code=status_code, url=url),
        "JSONResponse": lambda status_code=200, content=None, headers=None: _DummyResponse(
            status_code=status_code, content=content, headers=headers
        ),
        "SalesLLMClient": SalesLLMClient,
        "SalesLLMRequest": object,
        "HealthResponse": HealthResponse,
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


def test_backend_main_retires_ingest_routes_and_loads_no_legacy_rag(tmp_path: Path) -> None:
    """Importing `backend.main` loads zero legacy RAG/ingestion modules and omits retired `/api/ingest*` routes."""
    probe = _run_probe(
        "import json, sys; "
        "import backend.main; "
        "import backend.sales_ui.router; "
        "prohibited = ('ai_engine', 'ai_engine.rag_retriever', 'ai_engine.data_ingestor', "
        "'ai_engine.ingest_queue', 'chromadb', "
        "'langchain_core', 'langchain_ollama', 'langchain_chroma', 'ollama'); "
        "loaded = {m: (m in sys.modules) for m in prohibited}; "
        "routes = sorted({getattr(r, 'path', '') for r in backend.main.app.routes}); "
        "rag_initialized = getattr(backend.main.app.state, 'rag_retriever', None) is not None; "
        "print(json.dumps({'loaded': loaded, 'rag_initialized': rag_initialized, 'routes': routes}))",
        tmp_path=tmp_path,
    )
    assert all(v is False for v in probe["loaded"].values()), f"Legacy module loaded: {probe['loaded']}"
    assert probe["rag_initialized"] is False

    registered = set(probe["routes"])
    assert RETIRED_INGEST_ROUTES.isdisjoint(registered), f"Retired routes still registered: {RETIRED_INGEST_ROUTES & registered}"
    assert {"/health", "/api/auth/login", "/api/sales/test-llm", "/api/sales/analyze-website", "/api/sales/discover"}.issubset(
        registered
    )


def test_static_source_boundaries_backend_settings_and_auth_preserved() -> None:
    """Verify zero active backend references to legacy RAG/ingestion, preserved auth helpers, and clean Settings UI."""
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

    # 2. backend/main.py has zero imports or references to legacy RAG/ingestion anywhere (top-level or inline)
    main_src = (REPO_ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    main_tree = ast.parse(main_src, filename="backend/main.py")
    for node in ast.walk(main_tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert not a.name.startswith(("ai_engine", "chromadb", "langchain")), f"Forbidden import {a.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(("ai_engine", "chromadb", "langchain")), f"Forbidden import {node.module}"

    func_names = {
        node.name
        for node in main_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for retired_fn in (
        "get_rag_retriever",
        "_get_legacy_ingest_queue",
        "_get_legacy_data_ingestor_cls",
        "_run_approved_ingest",
        "submit_ingest_request",
        "list_knowledge_queue",
        "approve_knowledge_request",
        "reject_knowledge_request",
        "clear_queue_history",
        "get_submission_status",
    ):
        assert retired_fn not in func_names, f"Retired function still present: {retired_fn}"

    assert "/api/ingest" not in main_src
    assert "_rag_retriever_lock" not in main_src
    assert "chromadb_connected" not in main_src
    assert "indexed_rules_count" not in main_src
    assert "login_admin" in func_names
    assert "health_check" in func_names
    assert "operator_security_middleware" in func_names

    # 3. backend/auth.py preserves admin authentication & JWT helpers
    auth_tree = ast.parse((REPO_ROOT / "backend" / "auth.py").read_text(encoding="utf-8"), filename="backend/auth.py")
    auth_funcs = {n.name for n in auth_tree.body if isinstance(n, ast.FunctionDef)}
    assert {"authenticate_admin", "create_access_token", "verify_admin_token"}.issubset(auth_funcs)

    # 4. backend/sales_ui/router.py and settings.html remain free of chromadb while keeping active indicators
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


def test_decoupled_health_check_without_rag_or_chromadb() -> None:
    """`health_check` succeeds without ChromaDB/ai_engine, handles Ollama failures, and raises 503 if ollama missing."""
    sync_ok = MagicMock()
    sync_ok.list.return_value = SimpleNamespace(models=[SimpleNamespace(model="qwen2.5:1.5b")])
    llm = SalesLLMClient(default_model="qwen2.5-coder:1.5b", sync_client=sync_ok)
    fake_app = SimpleNamespace(state=SimpleNamespace())
    ns = _load_backend_functions(
        {"_ensure_ollama_dependency_available", "get_llm_client", "health_check"},
        fake_app=fake_app,
        llm_client=llm,
    )

    # 1. Even with chromadb and ai_engine blocked in sys.modules, health_check works cleanly
    with patch.dict(sys.modules, {"chromadb": None, "ai_engine": None, "ai_engine.rag_retriever": None}):
        resp = ns["health_check"]()
        assert isinstance(resp, HealthResponse)
        assert resp.status == "healthy"
        assert resp.ollama_connected is True
        assert resp.available_models == ["qwen2.5:1.5b"]
        assert resp.version == "0.1.0"
        dumped = resp.model_dump()
        assert "chromadb_connected" not in dumped
        assert "indexed_rules_count" not in dumped
        assert not hasattr(fake_app.state, "rag_retriever")

        # 2. Ollama unreachable -> status="unhealthy", ollama_connected=False
        sync_down = MagicMock()
        sync_down.list.side_effect = ConnectionError("ollama offline")
        fake_app.state.llm_client = SalesLLMClient(sync_client=sync_down)
        resp_down = ns["health_check"]()
        assert resp_down.status == "unhealthy"
        assert resp_down.ollama_connected is False
        assert resp_down.available_models == []

    # 3. Missing `ollama` package -> HTTP 503 preserved
    with patch.dict(sys.modules, {"ollama": None}):
        with pytest.raises(_DummyHTTPException) as exc_503:
            ns["health_check"]()
        assert exc_503.value.status_code == 503
        assert "Ollama" in exc_503.value.detail


def test_operator_middleware_removes_retired_ingest_exemption_and_preserves_auth() -> None:
    """`operator_security_middleware` no longer exempts `/api/ingest*` while preserving public & protected routes."""
    ns = _load_backend_functions({"operator_security_middleware"})
    middleware = ns["operator_security_middleware"]

    fake_auth_mod = ModuleType("backend.operator_auth")
    fake_auth_svc = SimpleNamespace(get_user_count=lambda: 1)

    async def _no_user(_request: Any) -> None:
        return None

    fake_auth_mod.get_auth_service = lambda: fake_auth_svc  # type: ignore[attr-defined]
    fake_auth_mod.SESSION_COOKIE_NAME = "sales_session"  # type: ignore[attr-defined]
    fake_auth_mod.get_current_user_optional = _no_user  # type: ignore[attr-defined]

    called_paths: list[str] = []

    async def _call_next(req: Any) -> _DummyResponse:
        called_paths.append(req.url.path)
        return _DummyResponse(status_code=200)

    def _make_req(path: str, method: str = "GET") -> Any:
        return SimpleNamespace(
            url=SimpleNamespace(path=path, query=""),
            method=method,
            headers={"accept": "application/json"},
            state=SimpleNamespace(),
        )

    with patch.dict(sys.modules, {"backend.operator_auth": fake_auth_mod}):
        # Surviving public endpoints pass through
        for pub_path in ("/health", "/api/auth/login", "/sales/login", "/static/app.css"):
            res = asyncio.run(middleware(_make_req(pub_path), _call_next))
            assert res.status_code == 200
        assert called_paths == ["/health", "/api/auth/login", "/sales/login", "/static/app.css"]

        # Retired `/api/ingest*` paths are NO LONGER exempted (blocked with 401 when operator accounts exist)
        for retired_path in ("/api/ingest", "/api/ingest/queue", "/api/ingest/status/req_1"):
            res = asyncio.run(middleware(_make_req(retired_path, method="POST"), _call_next))
            assert res.status_code == 401
        assert "/api/ingest" not in called_paths
