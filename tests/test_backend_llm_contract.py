"""
tests/test_backend_llm_contract.py
AST-isolated contract, lifecycle, payload-parity, and dependency-audit tests for
`backend/main.py`'s native `SalesLLMClient` migration (`lifespan`, `get_llm_client`,
`get_sales_llm_client`, `health_check`, `test_sales_llm`, and `generate_email_drafts`).

Zero FastAPI server startup, zero operational SQLite access, zero live Ollama/ChromaDB calls.
"""

from __future__ import annotations

import ast
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.schemas import HealthResponse, SalesLLMTestRequest
from sales_engine.llm_client import (
    SALES_ANALYSIS_SYSTEM_PROMPT,
    SalesLLMClient,
    SalesLLMRequest,
)
from tests.fixtures.historical_llm_contracts import (
    HISTORICAL_SALES_SYSTEM_PROMPT,
    expected_ollama_chat_payload,
)
from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent


class _DummyHTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _load_backend_llm_namespace(
    *,
    fake_app: Any | None = None,
    sales_llm_cls: Any = SalesLLMClient,
    sales_llm_req_cls: Any = SalesLLMRequest,
) -> dict[str, Any]:
    """Compile real Backend LLM lifecycle and route handlers from `backend/main.py` AST."""
    main_path = REPO_ROOT / "backend" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))

    wanted_locks = {"_llm_client_lock", "_sales_llm_client_lock"}
    wanted_funcs = {
        "lifespan",
        "_ensure_ollama_dependency_available",
        "get_llm_client",
        "get_sales_llm_client",
        "health_check",
        "test_sales_llm",
        "generate_email_drafts",
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
            keep_decorators = node.decorator_list if node.name == "lifespan" else []
            cloned = ast.copy_location(
                type(node)(
                    name=node.name,
                    args=node.args,
                    body=node.body,
                    decorator_list=keep_decorators,
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
    ns: dict[str, Any] = {
        "app": app_obj,
        "threading": threading,
        "asynccontextmanager": asynccontextmanager,
        "FastAPI": object,
        "HTTPException": _DummyHTTPException,
        "SalesLLMClient": sales_llm_cls,
        "SalesLLMRequest": sales_llm_req_cls,
        "HealthResponse": HealthResponse,
        "SalesLLMTestRequest": SalesLLMTestRequest,
        "GenerateDraftsRequest": object,
    }
    mod = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(mod)
    exec(compile(mod, filename=str(main_path), mode="exec"), ns)
    return ns


def test_backend_llm_lifecycle_profiles_concurrency_and_shutdown_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify dual native profiles, startup init failure isolation, HTTP 503, concurrency, and lifecycle refresh."""
    fake_app = SimpleNamespace(state=SimpleNamespace())
    ns = _load_backend_llm_namespace(fake_app=fake_app)

    # 1. Missing/uninitialized state lazily creates distinct configured native profiles without live Ollama calls
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:21434")
    monkeypatch.setenv("SALES_LLM_MODEL", "sales-custom-model")
    gw_client = ns["get_llm_client"]()
    sales_client = ns["get_sales_llm_client"]()

    assert isinstance(gw_client, SalesLLMClient)
    assert isinstance(sales_client, SalesLLMClient)
    assert gw_client is not sales_client
    assert gw_client.default_model == "qwen2.5-coder:1.5b"
    assert gw_client.host == "http://127.0.0.1:21434"
    assert gw_client._sync_client is None and gw_client._async_client is None
    assert sales_client.default_model == "sales-custom-model"
    assert sales_client.host == "http://127.0.0.1:21434"
    assert sales_client._sync_client is None and sales_client._async_client is None

    # Repeated calls reuse cached instances
    assert ns["get_llm_client"]() is gw_client
    assert ns["get_sales_llm_client"]() is sales_client

    # 2. Missing SalesLLMClient or missing/incomplete `ollama` package raises HTTP 503 across gateway endpoints
    ns_unavail = _load_backend_llm_namespace(sales_llm_cls=None)
    with pytest.raises(_DummyHTTPException) as exc_503:
        ns_unavail["get_llm_client"]()
    assert exc_503.value.status_code == 503
    assert "Ollama" in exc_503.value.detail

    # Missing `ollama` module (even when SalesLLMClient imported lazily and state is cached) -> HTTP 503
    with patch.dict(sys.modules, {"ollama": None}):
        with pytest.raises(_DummyHTTPException) as exc_mod:
            ns["health_check"]()
        assert exc_mod.value.status_code == 503
        with pytest.raises(_DummyHTTPException) as exc_diag:
            asyncio.run(ns["test_sales_llm"](SalesLLMTestRequest(text="Acme BIM")))
        assert exc_diag.value.status_code == 503

    # `ollama` present without required Client/AsyncClient symbols -> HTTP 503
    with patch.dict(sys.modules, {"ollama": SimpleNamespace()}):
        with pytest.raises(_DummyHTTPException) as exc_sym:
            ns["get_llm_client"]()
        assert exc_sym.value.status_code == 503

    # 3. Lifespan startup failure isolates cleanly (sets None without crashing startup)
    ns_boom = _load_backend_llm_namespace(
        fake_app=SimpleNamespace(state=SimpleNamespace()),
        sales_llm_cls=MagicMock(side_effect=RuntimeError("init boom")),
    )

    async def run_failing_startup() -> None:
        async with ns_boom["lifespan"](ns_boom["app"]):
            assert ns_boom["app"].state.llm_client is None
            assert ns_boom["app"].state.sales_llm_client is None

    asyncio.run(run_failing_startup())

    # 4. Concurrent first initialization of get_llm_client yields single shared instance
    fake_app.state.llm_client = None
    barrier = threading.Barrier(4)
    collected: list[Any] = []

    def worker() -> None:
        barrier.wait(timeout=5.0)
        collected.append(ns["get_llm_client"]())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive()
    assert len(collected) == 4 and all(c is collected[0] for c in collected)

    # 5. Shutdown clears both clients and subsequent lifespan refreshes OLLAMA_HOST / SALES_LLM_MODEL
    async def run_two_lifecycles() -> tuple[tuple[str, str], tuple[str, str]]:
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11111")
        monkeypatch.setenv("SALES_LLM_MODEL", "model-v1")
        async with ns["lifespan"](fake_app):
            c1 = (fake_app.state.llm_client.host, fake_app.state.sales_llm_client.default_model)
        assert fake_app.state.llm_client is None
        assert fake_app.state.sales_llm_client is None

        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:22222")
        monkeypatch.setenv("SALES_LLM_MODEL", "model-v2")
        async with ns["lifespan"](fake_app):
            c2 = (fake_app.state.llm_client.host, fake_app.state.sales_llm_client.default_model)
        assert fake_app.state.llm_client is None
        assert fake_app.state.sales_llm_client is None
        return c1, c2

    first_cfg, second_cfg = asyncio.run(run_two_lifecycles())
    assert first_cfg == ("http://127.0.0.1:11111", "model-v1")
    assert second_cfg == ("http://127.0.0.1:22222", "model-v2")


def test_backend_health_endpoint_preserves_ollama_schema_and_decoupled_status() -> None:
    """Verify /health healthy, unhealthy, list-exception, and zero-ChromaDB decoupled semantics."""
    # 1. Ollama healthy -> status="healthy"
    sync_ok = MagicMock()
    sync_ok.list.return_value = SimpleNamespace(
        models=[SimpleNamespace(model="qwen2.5:1.5b"), SimpleNamespace(model="llama3")]
    )
    fake_app = SimpleNamespace(
        state=SimpleNamespace(llm_client=SalesLLMClient(default_model="qwen2.5-coder:1.5b", sync_client=sync_ok))
    )
    ns = _load_backend_llm_namespace(fake_app=fake_app)
    resp = ns["health_check"]()
    assert isinstance(resp, HealthResponse)
    assert resp.status == "healthy"
    assert resp.ollama_connected is True
    assert resp.available_models == ["qwen2.5:1.5b", "llama3"]
    assert resp.version == "0.1.0"
    assert "chromadb_connected" not in resp.model_dump()
    assert "indexed_rules_count" not in resp.model_dump()

    # 2. Ollama unavailable -> status="unhealthy"
    sync_down = MagicMock()
    sync_down.list.side_effect = ConnectionError("ollama down")
    fake_app.state.llm_client = SalesLLMClient(sync_client=sync_down)
    resp_down = ns["health_check"]()
    assert resp_down.status == "unhealthy"
    assert resp_down.ollama_connected is False
    assert resp_down.available_models == []

    # 3. is_healthy() succeeds then list_available_models() raises -> ollama_connected False, status="unhealthy"
    sync_flaky = MagicMock()
    sync_flaky.list.side_effect = [SimpleNamespace(models=[]), RuntimeError("list error")]
    fake_app.state.llm_client = SalesLLMClient(sync_client=sync_flaky)
    resp_bad = ns["health_check"]()
    assert resp_bad.status == "unhealthy"
    assert resp_bad.ollama_connected is False
    assert resp_bad.available_models == []


def test_backend_test_llm_endpoint_payload_parity_and_error_handling() -> None:
    """Verify /api/sales/test-llm matches frozen historical payload, llama3 override, errors, and empty/None handling."""
    async_mock = MagicMock()
    async_mock.chat = AsyncMock(
        return_value=SimpleNamespace(message=SimpleNamespace(content="  Sells BIM automation to AEC firms.  "))
    )
    fake_app = SimpleNamespace(
        state=SimpleNamespace(llm_client=SalesLLMClient(default_model="qwen2.5-coder:1.5b", async_client=async_mock))
    )
    ns = _load_backend_llm_namespace(fake_app=fake_app)

    req = SalesLLMTestRequest(text="Acme provides Revit automation for engineering studios.")
    result = asyncio.run(ns["test_sales_llm"](req))
    assert result == {
        "status": "ok",
        "model": "llama3",
        "response": "Sells BIM automation to AEC firms.",
    }

    native_kwargs = async_mock.chat.call_args.kwargs
    assert native_kwargs["model"] == "llama3"
    assert native_kwargs["options"] == {"temperature": 0.1, "num_ctx": 4096}
    assert [m["role"] for m in native_kwargs["messages"]] == ["system", "user"]
    assert native_kwargs["messages"][0]["content"] == HISTORICAL_SALES_SYSTEM_PROMPT
    assert native_kwargs == expected_ollama_chat_payload(
        user_prompt=native_kwargs["messages"][1]["content"],
        task="sales_analysis",
        model="llama3",
        temperature=0.1,
        num_ctx=4096,
    )

    # Provider exception -> status="error"
    err_async = MagicMock()
    err_async.chat = AsyncMock(side_effect=TimeoutError("ollama timeout"))
    fake_app.state.llm_client = SalesLLMClient(async_client=err_async)
    err_res = asyncio.run(ns["test_sales_llm"](req))
    assert err_res == {"status": "error", "model": "llama3", "response": "ollama timeout"}

    # Invalid, malformed, empty, and whitespace-only provider responses must return status="error"
    for bad_payload in (
        None,
        SimpleNamespace(),
        SimpleNamespace(message=None),
        SimpleNamespace(message=SimpleNamespace(content=None)),
        SimpleNamespace(message=SimpleNamespace(content="")),
        SimpleNamespace(message=SimpleNamespace(content="   \n\t  ")),
    ):
        bad_async = MagicMock()
        bad_async.chat = AsyncMock(return_value=bad_payload)
        fake_app.state.llm_client = SalesLLMClient(async_client=bad_async)
        bad_res = asyncio.run(ns["test_sales_llm"](req))
        assert bad_res["status"] == "error"
        assert bad_res["model"] == "llama3"
        assert bad_res["response"] == "LLM provider returned an empty or malformed response."
        assert bad_async.chat.call_count == 1


def test_backend_main_has_no_direct_legacy_llm_client_imports(tmp_path: Path) -> None:
    """Verify backend/main.py contains zero imports or references to ai_engine.llm_client / BIMLLMClient."""
    proc = run_isolated_subprocess(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import ast, json, pathlib; "
                "src = pathlib.Path('backend/main.py').read_text(encoding='utf-8'); "
                "tree = ast.parse(src); "
                "bad_imports = [n.module for n in ast.walk(tree) "
                "if isinstance(n, ast.ImportFrom) and n.module and 'ai_engine.llm_client' in n.module]; "
                "print(json.dumps({'bad_imports': bad_imports, "
                "'has_bim_symbol': 'BIMLLMClient' in src, "
                "'has_codegen_req': 'CodeGenerationRequest' in src, "
                "'has_sales_client': 'SalesLLMClient' in src}))"
            ),
        ],
        isolated_db_path=tmp_path / "isolated_backend_llm_probe.db",
        trusted_dir=tmp_path,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(proc.stdout.strip()) == {
        "bad_imports": [],
        "has_bim_symbol": False,
        "has_codegen_req": False,
        "has_sales_client": True,
    }
