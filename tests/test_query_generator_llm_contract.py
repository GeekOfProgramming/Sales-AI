"""
tests/test_query_generator_llm_contract.py
Isolated unit, payload-parity, fallback, and import-boundary tests for
`sales_engine.discovery.query_generator.QueryGenerator`.

Zero operational DB access, zero live search/Ollama/network calls, zero FastAPI startup.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.schemas import WebsiteProfile
from sales_engine.discovery.query_generator import QueryGenerator
from sales_engine.llm_client import SalesLLMClient
from tests.fixtures.historical_llm_contracts import (
    HISTORICAL_SALES_SYSTEM_PROMPT,
    expected_ollama_chat_payload,
)
from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_provider_response(text: str | None) -> SimpleNamespace:
    return SimpleNamespace(message=SimpleNamespace(content=text))


def _sample_profile() -> WebsiteProfile:
    return WebsiteProfile(
        company_name="Acme BIM",
        services=["BIM Automation"],
        target_industries=["Engineering", "Architecture"],
        target_company_types=["AEC Firms"],
        buyer_roles=["Head of Digital Delivery"],
        primary_job_signals=["BIM Manager", "Revit API Developer", "BIM Coordinator"],
        secondary_job_signals=["Revit", "Python", "ISO 19650"],
        keywords=["BIM", "Revit"],
    )


def test_query_generator_llm_disabled_deterministic_and_no_live_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DISCOVERY_USE_LLM is disabled, no provider client is constructed or invoked."""
    monkeypatch.setenv("DISCOVERY_USE_LLM", "false")
    async_mock = MagicMock()
    async_mock.chat = AsyncMock()
    sync_mock = MagicMock()

    generator = QueryGenerator(
        max_queries=12,
        llm_client=SalesLLMClient(sync_client=sync_mock, async_client=async_mock),
    )
    resp = asyncio.run(generator.generate_queries(_sample_profile(), ["Italy", "Germany"]))

    assert len(resp.queries) > 0
    assert len(resp.queries) <= 12
    async_mock.chat.assert_not_called()

    # Default instantiation stays lazy and does not instantiate Ollama clients when disabled
    default_gen = QueryGenerator(max_queries=12)
    default_resp = asyncio.run(default_gen.generate_queries(_sample_profile(), ["Italy", "Germany"]))
    assert default_gen.llm_client._sync_client is None
    assert default_gen.llm_client._async_client is None
    assert [q.model_dump() for q in default_resp.queries] == [q.model_dump() for q in resp.queries]


def test_query_generator_llm_enabled_payload_parity_and_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled DISCOVERY_USE_LLM sends frozen historical Ollama payload and preserves limits/order."""
    monkeypatch.setenv("DISCOVERY_USE_LLM", "true")
    monkeypatch.setenv("SALES_LLM_MODEL", "qwen2.5:1.5b")

    llm_payload = {
        "queries": [
            {"query": '"BIM Manager" custom priority signal Italy', "type": "general_job", "priority": 10},
            {"query": '"BIM Manager" custom priority signal Italy', "type": "general_job", "priority": 9},
            {"query": 'site:jobs.lever.co "Head of Digital Delivery"', "type": "ats_targeted", "priority": 10},
        ]
    }
    raw_json = f"```json\n{json.dumps(llm_payload)}\n```"

    native_async = MagicMock()
    native_async.chat = AsyncMock(return_value=_make_provider_response(raw_json))
    native_client = SalesLLMClient(timeout=180.0, async_client=native_async)
    generator = QueryGenerator(max_queries=11, llm_client=native_client)

    profile = _sample_profile()
    resp = asyncio.run(generator.generate_queries(profile, ["Italy"]))

    assert native_async.chat.call_count == 1
    native_kwargs = native_async.chat.call_args.kwargs
    assert native_kwargs["model"] == "qwen2.5:1.5b"
    assert native_kwargs["options"] == {"temperature": 0.1, "num_ctx": 2048}
    assert [m["role"] for m in native_kwargs["messages"]] == ["system", "user"]
    assert native_kwargs["messages"][0]["content"] == HISTORICAL_SALES_SYSTEM_PROMPT

    assert native_kwargs == expected_ollama_chat_payload(
        user_prompt=native_kwargs["messages"][1]["content"],
        task="sales_analysis",
        model="qwen2.5:1.5b",
        temperature=0.1,
        num_ctx=2048,
    )

    # Deduplication, unapproved buyer_role filtering, sorting, and max_queries cap preserved
    queries_text = [q.query for q in resp.queries]
    assert queries_text.count('"BIM Manager" custom priority signal Italy') == 1
    assert all("Head of Digital Delivery" not in q for q in queries_text)
    priorities = [q.priority for q in resp.queries]
    assert priorities == sorted(priorities, reverse=True)
    assert len(resp.queries) <= 11


@pytest.mark.parametrize(
    "side_effect",
    [
        [_make_provider_response("not json at all")],
        [_make_provider_response("")],
        [_make_provider_response(None)],
        [RuntimeError("ollama unreachable")],
    ],
)
def test_query_generator_fallbacks_match_deterministic_baseline(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: list[object],
) -> None:
    """Invalid JSON, empty/None response, and provider errors cleanly fall back to intent queries."""
    profile = _sample_profile()
    monkeypatch.setenv("DISCOVERY_USE_LLM", "false")
    baseline = asyncio.run(
        QueryGenerator(
            max_queries=15,
            llm_client=SalesLLMClient(async_client=MagicMock()),
        ).generate_queries(profile, ["Germany"])
    )

    monkeypatch.setenv("DISCOVERY_USE_LLM", "true")
    failing_async = MagicMock()
    failing_async.chat = AsyncMock(side_effect=side_effect)
    fallback_resp = asyncio.run(
        QueryGenerator(
            max_queries=15,
            llm_client=SalesLLMClient(async_client=failing_async),
        ).generate_queries(profile, ["Germany"])
    )

    assert failing_async.chat.call_count == 1
    assert [q.model_dump() for q in fallback_resp.queries] == [q.model_dump() for q in baseline.queries]


def test_query_generator_import_isolation(tmp_path: Path) -> None:
    """Importing QueryGenerator must not load ai_engine, ollama, chromadb, scrapling, or sqlite3."""
    proc = run_isolated_subprocess(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys; "
                "import sales_engine.discovery.query_generator; "
                "loaded = {m: (m in sys.modules) for m in ("
                "'ai_engine', 'ai_engine.llm_client', 'ollama', 'chromadb', "
                "'scrapling', 'langchain_core', 'sqlite3')}; "
                "print(json.dumps(loaded))"
            ),
        ],
        isolated_db_path=tmp_path / "isolated_qg_probe.db",
        trusted_dir=tmp_path,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(proc.stdout.strip()) == {
        "ai_engine": False,
        "ai_engine.llm_client": False,
        "ollama": False,
        "chromadb": False,
        "scrapling": False,
        "langchain_core": False,
        "sqlite3": False,
    }
