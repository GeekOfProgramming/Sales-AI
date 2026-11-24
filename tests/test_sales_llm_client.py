"""
tests/test_sales_llm_client.py
Isolated unit, payload-parity, and import-boundary tests for `sales_engine.llm_client`
and migrated `sales_engine.analysis.job_analyzer.JobAnalyzer`.

Zero operational DB access, zero live Ollama/network calls, zero FastAPI startup.
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
from sales_engine.analysis.job_analyzer import JobAnalyzer
from sales_engine.llm_client import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_SALES_MODEL,
    SALES_ANALYSIS_SYSTEM_PROMPT,
    SALES_OUTREACH_SYSTEM_PROMPT,
    SalesLLMClient,
    SalesLLMRequest,
    SalesLLMResponse,
)
from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_provider_response(text: str | None) -> SimpleNamespace:
    return SimpleNamespace(message=SimpleNamespace(content=text))


def test_request_defaults_message_order_and_prompt_profiles() -> None:
    """Verify request construction, [system, user] ordering, and Sales-AI prompt profiles."""
    req_analysis = SalesLLMRequest(
        user_prompt="Analyze this posting",
        context="Return JSON only.",
        task="sales_analysis",
        output_format="json",
    )
    msgs_analysis = SalesLLMClient.build_messages(req_analysis)
    assert [m["role"] for m in msgs_analysis] == ["system", "user"]
    assert msgs_analysis[0]["content"] == (
        f"{SALES_ANALYSIS_SYSTEM_PROMPT}\n\n[SALES KNOWLEDGE CONTEXT]:\nReturn JSON only."
    )
    assert msgs_analysis[1]["content"] == "Analyze this posting"

    req_outreach = SalesLLMRequest(
        user_prompt="Draft outreach",
        context="Keep under 90 words.",
        task="sales_outreach",
    )
    msgs_outreach = SalesLLMClient.build_messages(req_outreach)
    assert msgs_outreach[0]["content"] == (
        f"{SALES_OUTREACH_SYSTEM_PROMPT}\n\n[OUTREACH GUIDELINES & CONSTRAINTS]:\nKeep under 90 words."
    )

    req_custom = SalesLLMRequest(
        user_prompt="Custom user",
        system_prompt="  Explicit system prompt  ",
    )
    msgs_custom = SalesLLMClient.build_messages(req_custom)
    assert msgs_custom[0]["content"] == "Explicit system prompt"
    assert "Revit" not in msgs_analysis[0]["content"]
    assert "Revit" not in msgs_outreach[0]["content"]


def test_host_model_and_timeout_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify OLLAMA_HOST, SALES_LLM_MODEL, explicit overrides, and timeout defaults."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("SALES_LLM_MODEL", raising=False)
    default_client = SalesLLMClient()
    assert default_client.host == DEFAULT_OLLAMA_HOST
    assert default_client.default_model == DEFAULT_SALES_MODEL
    assert default_client.timeout == 180.0

    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:22434")
    monkeypatch.setenv("SALES_LLM_MODEL", "qwen2.5:3b")
    env_client = SalesLLMClient(timeout=45.0)
    assert env_client.host == "http://127.0.0.1:22434"
    assert env_client.default_model == "qwen2.5:3b"
    assert env_client.timeout == 45.0

    explicit_client = SalesLLMClient(host="http://127.0.0.1:33434", default_model="llama3")
    assert explicit_client.host == "http://127.0.0.1:33434"
    assert explicit_client.default_model == "llama3"


def test_sync_generate_options_extraction_and_health_checks() -> None:
    """Verify sync generate(), JSON fence extraction, options forwarding, and health/model helpers."""
    sync_mock = MagicMock()
    sync_mock.chat.return_value = _make_provider_response('```json\n{"ok": true}\n```')
    sync_mock.list.return_value = SimpleNamespace(
        models=[SimpleNamespace(model="qwen2.5:1.5b"), SimpleNamespace(model="llama3")]
    )

    client = SalesLLMClient(default_model="qwen2.5:1.5b", sync_client=sync_mock)
    req = SalesLLMRequest(
        user_prompt="Extract JSON",
        output_format="json",
        temperature=0.25,
        num_ctx=2048,
    )
    resp = client.generate(req, model_name="custom-model")

    assert isinstance(resp, SalesLLMResponse)
    assert resp.raw_text == '```json\n{"ok": true}\n```'
    assert resp.content == '{"ok": true}'
    assert resp.model == "custom-model"
    assert resp.duration_seconds >= 0.0

    sync_mock.chat.assert_called_once_with(
        model="custom-model",
        messages=SalesLLMClient.build_messages(req),
        options={"temperature": 0.25, "num_ctx": 2048},
    )
    assert client.is_healthy() is True
    assert client.list_available_models() == ["qwen2.5:1.5b", "llama3"]


def test_async_generate_timeout_exception_and_empty_response_handling() -> None:
    """Verify async generate_async(), provider exceptions, and empty/malformed responses."""
    async_mock = MagicMock()
    async_mock.chat = AsyncMock(
        side_effect=[
            _make_provider_response(None),
            SimpleNamespace(message=None),
            TimeoutError("provider timed out"),
        ]
    )
    client = SalesLLMClient(async_client=async_mock)
    req = SalesLLMRequest(user_prompt="Hello", output_format="text")

    resp_none = asyncio.run(client.generate_async(req))
    assert resp_none.raw_text == ""
    assert resp_none.content == ""

    resp_missing_msg = asyncio.run(client.generate_async(req))
    assert resp_missing_msg.raw_text == ""
    assert resp_missing_msg.content == ""

    with pytest.raises(TimeoutError, match="provider timed out"):
        asyncio.run(client.generate_async(req))

    failing_sync = MagicMock()
    failing_sync.list.side_effect = RuntimeError("connection refused")
    unhealthy_client = SalesLLMClient(sync_client=failing_sync)
    assert unhealthy_client.is_healthy() is False
    with pytest.raises(RuntimeError, match="Failed to query Ollama models"):
        unhealthy_client.list_available_models()


def test_payload_parity_with_legacy_bim_client_for_sales_analysis() -> None:
    """Verify identical Ollama chat payload between legacy BIMLLMClient and native SalesLLMClient."""
    from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

    prompt = "JOB POSTING TITLE: BIM Manager\nCOMPANY: Acme\n\nDESCRIPTION:\nRevit API automation"
    context = "Analyze this job posting and return JSON."

    legacy_async = MagicMock()
    legacy_async.chat = AsyncMock(return_value=_make_provider_response('{"technologies": ["Revit"]}'))
    legacy_client = BIMLLMClient.__new__(BIMLLMClient)
    legacy_client.default_model = "qwen2.5:1.5b"
    legacy_client._async_client = legacy_async

    native_async = MagicMock()
    native_async.chat = AsyncMock(return_value=_make_provider_response('{"technologies": ["Revit"]}'))
    native_client = SalesLLMClient(default_model="qwen2.5:1.5b", async_client=native_async)

    legacy_req = CodeGenerationRequest(
        user_prompt=prompt,
        context_rules=context,
        environment="sales",
        language="json",
    )
    native_req = SalesLLMRequest(
        user_prompt=prompt,
        context=context,
        task="sales_analysis",
        output_format="json",
    )

    legacy_resp = asyncio.run(legacy_client.generate_code_async(legacy_req, model_name="qwen2.5:1.5b"))
    native_resp = asyncio.run(native_client.generate_async(native_req, model_name="qwen2.5:1.5b"))

    assert native_async.chat.call_args.kwargs == legacy_async.chat.call_args.kwargs
    assert native_resp.content == legacy_resp.extracted_code
    assert native_resp.raw_text == legacy_resp.raw_response
    assert native_resp.model == legacy_resp.model


def test_job_analyzer_migration_di_retry_and_error_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify migrated JobAnalyzer preserves DI, retry repair, StructuredJob output, and error semantics."""
    monkeypatch.setenv("SALES_LLM_MODEL", "test-sales-model")

    async_mock = MagicMock()
    async_mock.chat = AsyncMock(
        side_effect=[
            _make_provider_response("not valid json"),
            _make_provider_response(
                '```json\n'
                '{"technologies": ["Revit", "Python"], "seniority": "Senior", '
                '"remote_status": "hybrid", "requirements": ["Revit API"], '
                '"relevant_signals": [{"signal": "Expanding BIM", "evidence": "Revit API automation"}]}\n'
                '```'
            ),
        ]
    )
    injected_client = SalesLLMClient(async_client=async_mock)
    analyzer = JobAnalyzer(llm_client=injected_client)
    profile = WebsiteProfile(services=["BIM Automation"])

    raw_job = {
        "title": "Senior BIM Developer",
        "company": "Acme Engineering",
        "url": "https://example.com/jobs/1",
        "source": "lever",
        "description": "Looking for Revit API automation specialist.",
        "raw_metadata": {"source_company_key": "acme-eng"},
    }

    job = asyncio.run(analyzer.analyze_job(raw_job, profile=profile))
    assert job.company_name == "Acme Engineering"
    assert job.source_company_key == "acme-eng"
    assert job.technologies == ["Revit", "Python"]
    assert job.seniority == "Senior"
    assert job.remote_status == "hybrid"
    assert len(job.relevant_signals) == 1
    assert async_mock.chat.call_count == 2
    assert "IMPORTANT JSON REPAIR" in async_mock.chat.call_args_list[1].kwargs["messages"][1]["content"]
    assert async_mock.chat.call_args_list[0].kwargs["model"] == "test-sales-model"

    # Failure after 2 attempts preserves exact exception message prefix
    failing_async = MagicMock()
    failing_async.chat = AsyncMock(side_effect=[_make_provider_response("bad1"), _make_provider_response("bad2")])
    failing_analyzer = JobAnalyzer(llm_client=SalesLLMClient(async_client=failing_async))
    with pytest.raises(Exception, match="Failed to analyze job via LLM"):
        asyncio.run(failing_analyzer.analyze_job(raw_job))


def test_native_client_and_job_analyzer_import_isolation(tmp_path: Path) -> None:
    """Importing SalesLLMClient and JobAnalyzer must not load ai_engine, ollama, chromadb, scrapling, or sqlite3."""
    proc = run_isolated_subprocess(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys; "
                "import sales_engine.llm_client; "
                "import sales_engine.analysis.job_analyzer; "
                "loaded = {m: (m in sys.modules) for m in ("
                "'ai_engine', 'ai_engine.llm_client', 'ollama', 'chromadb', "
                "'scrapling', 'langchain_core', 'sqlite3')}; "
                "print(json.dumps(loaded))"
            ),
        ],
        isolated_db_path=tmp_path / "isolated_llm_probe.db",
        trusted_dir=tmp_path,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    loaded = json.loads(proc.stdout.strip())
    assert loaded == {
        "ai_engine": False,
        "ai_engine.llm_client": False,
        "ollama": False,
        "chromadb": False,
        "scrapling": False,
        "langchain_core": False,
        "sqlite3": False,
    }
