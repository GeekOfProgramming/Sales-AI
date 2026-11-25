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
from tests.fixtures.historical_llm_contracts import (
    HISTORICAL_SALES_SYSTEM_PROMPT,
    expected_ollama_chat_payload,
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


def test_payload_parity_with_historical_sales_analysis_contract() -> None:
    """Verify native SalesLLMClient matches frozen historical Ollama chat payload for sales analysis."""
    prompt = "JOB POSTING TITLE: BIM Manager\nCOMPANY: Acme\n\nDESCRIPTION:\nRevit API automation"
    context = "Analyze this job posting and return JSON."
    raw_reply = '{"technologies": ["Revit"]}'

    native_async = MagicMock()
    native_async.chat = AsyncMock(return_value=_make_provider_response(raw_reply))
    native_client = SalesLLMClient(default_model="qwen2.5:1.5b", async_client=native_async)

    native_req = SalesLLMRequest(
        user_prompt=prompt,
        context=context,
        task="sales_analysis",
        output_format="json",
    )

    native_resp = asyncio.run(native_client.generate_async(native_req, model_name="qwen2.5:1.5b"))

    assert native_async.chat.call_args.kwargs == expected_ollama_chat_payload(
        user_prompt=prompt,
        task="sales_analysis",
        model="qwen2.5:1.5b",
        context=context,
        temperature=0.1,
        num_ctx=4096,
    )
    assert native_resp.content == '{"technologies": ["Revit"]}'
    assert native_resp.raw_text == raw_reply
    assert native_resp.model == "qwen2.5:1.5b"


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


def test_website_analyzer_two_pass_timeout_ctx_repair_and_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify WebsiteAnalyzer Pass A/B, 300s timeout, num_ctx 4096/2048, fence repair, DI, and frozen payload."""
    from sales_engine.analysis.website_analyzer import WebsiteAnalyzer

    monkeypatch.setenv("SALES_LLM_MODEL", "website-test-model")
    default_analyzer = WebsiteAnalyzer()
    assert default_analyzer.llm_client.timeout == 300.0
    assert default_analyzer.model_name == "website-test-model"

    pass_a_facts = {
        "company_name": "Acme BIM",
        "company_summary": "Acme BIM is a leading provider of BIM automation.",
        "offerings": [{"name": "Tech-Enabled BIM Services", "status": "active"}],
        "target_industries": ["AEC"],
        "target_company_types": ["Engineering Firms"],
        "pain_points": ["Manual coordination"],
        "client_testimonial_roles": ["Senior BIM Manager"],
        "internal_team_roles": ["Lead Algorithmic Engineer"],
        "technologies": ["Revit", "Python"],
        "standards": ["ISO 19650"],
        "keywords": ["BIM", "Revit"],
    }
    pass_b_signals = {
        "buyer_roles": ["Senior BIM Manager"],
        "primary_job_signals": ["BIM Coordinator", "Revit API Developer"],
        "secondary_job_signals": ["Revit", "Python"],
        "negative_signals": ["student-only"],
    }

    async_mock = MagicMock()
    async_mock.chat = AsyncMock(
        side_effect=[
            _make_provider_response("not valid json"),
            _make_provider_response(f"```json\n{json.dumps(pass_a_facts)}\n```"),
            _make_provider_response(json.dumps(pass_b_signals)),
        ]
    )
    analyzer = WebsiteAnalyzer(
        llm_client=SalesLLMClient(timeout=300.0, async_client=async_mock),
        model_name="override-model",
    )
    fetch_result = {
        "base_url": "https://acmebim.example",
        "pages": [
            {"url": "https://acmebim.example", "text": "Tech-Enabled BIM Services for AEC teams."},
            {"url": "https://acmebim.example/services", "text": "Revit API automation and ISO 19650."},
        ],
    }

    profile = asyncio.run(analyzer.analyze_website(fetch_result))
    assert isinstance(profile, WebsiteProfile)
    assert profile.company_name == "Acme BIM"
    assert profile.services == ["Tech-Enabled BIM Services"]
    assert "Senior BIM Manager" in profile.buyer_roles
    assert "BIM Coordinator" in profile.primary_job_signals
    assert async_mock.chat.call_count == 3

    call_pass_a_1 = async_mock.chat.call_args_list[0].kwargs
    call_pass_a_2 = async_mock.chat.call_args_list[1].kwargs
    call_pass_b = async_mock.chat.call_args_list[2].kwargs

    assert call_pass_a_1["model"] == "override-model"
    assert call_pass_a_1["options"] == {"temperature": 0.1, "num_ctx": 4096}
    assert call_pass_a_1["messages"][0]["content"] == HISTORICAL_SALES_SYSTEM_PROMPT
    assert "IMPORTANT JSON REPAIR:" in call_pass_a_2["messages"][1]["content"]
    assert call_pass_b["options"] == {"temperature": 0.1, "num_ctx": 2048}

    assert call_pass_a_1 == expected_ollama_chat_payload(
        user_prompt=call_pass_a_1["messages"][1]["content"],
        task="sales_analysis",
        model="override-model",
        temperature=0.1,
        num_ctx=4096,
    )
    assert call_pass_b == expected_ollama_chat_payload(
        user_prompt=call_pass_b["messages"][1]["content"],
        task="sales_analysis",
        model="override-model",
        temperature=0.1,
        num_ctx=2048,
    )


def test_website_analyzer_exhausted_retry_and_pass_b_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify exhausted Pass A retries raise ValueError and Pass B failures/None fall back to {}."""
    from sales_engine.analysis.website_analyzer import WebsiteAnalyzer

    monkeypatch.setenv("SALES_LLM_MODEL", "qwen2.5:1.5b")
    fetch_result = {
        "base_url": "https://acmebim.example",
        "pages": [{"url": "https://acmebim.example", "text": "BIM services."}],
    }

    # 1. Exhausted Pass A (including malformed None responses) raises ValueError
    fail_a_async = MagicMock()
    fail_a_async.chat = AsyncMock(
        side_effect=[_make_provider_response(None), _make_provider_response("still bad")]
    )
    analyzer_fail_a = WebsiteAnalyzer(llm_client=SalesLLMClient(async_client=fail_a_async))
    with pytest.raises(ValueError, match="Failed to parse LLM response into WebsiteProfile"):
        asyncio.run(analyzer_fail_a.analyze_website(fetch_result))
    assert fail_a_async.chat.call_count == 2

    # 2. Pass A succeeds, Pass B fails twice -> falls back cleanly to {} without crashing
    pass_a_facts = {
        "company_name": "Acme BIM",
        "company_summary": "Acme BIM provides engineering automation.",
        "services": ["BIM Automation"],
    }
    fallback_b_async = MagicMock()
    fallback_b_async.chat = AsyncMock(
        side_effect=[
            _make_provider_response(json.dumps(pass_a_facts)),
            TimeoutError("pass b timeout 1"),
            _make_provider_response(None),
        ]
    )
    analyzer_b = WebsiteAnalyzer(llm_client=SalesLLMClient(async_client=fallback_b_async))
    profile = asyncio.run(analyzer_b.analyze_website(fetch_result))
    assert profile.company_name == "Acme BIM"
    assert "BIM Manager" in profile.primary_job_signals
    assert fallback_b_async.chat.call_count == 3


def test_website_analyzer_import_isolation(tmp_path: Path) -> None:
    """Importing WebsiteAnalyzer must not load ai_engine, ollama, chromadb, scrapling, or sqlite3."""
    proc = run_isolated_subprocess(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys; "
                "import sales_engine.analysis.website_analyzer; "
                "loaded = {m: (m in sys.modules) for m in ("
                "'ai_engine', 'ai_engine.llm_client', 'ollama', 'chromadb', "
                "'scrapling', 'langchain_core', 'sqlite3')}; "
                "print(json.dumps(loaded))"
            ),
        ],
        isolated_db_path=tmp_path / "isolated_wa_probe.db",
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


def test_sync_payload_parity_with_historical_inbox_and_outreach_contracts() -> None:
    """Verify sync generate() matches frozen historical payloads across analysis (json/text) and outreach profiles."""
    cases = [
        # ReplyClassifier profile (temperature=0.0, json)
        (
            SalesLLMRequest(
                user_prompt="Classify reply",
                task="sales_analysis",
                output_format="json",
                temperature=0.0,
            ),
            "qwen2.5:1.5b",
            '```json\n{"category": "question", "confidence": 0.9, "reasoning": "asked about scope"}\n```',
            '{"category": "question", "confidence": 0.9, "reasoning": "asked about scope"}',
        ),
        # ReplySuggestionEngine profile (temperature=0.1, text)
        (
            SalesLLMRequest(
                user_prompt="Draft concise reply",
                task="sales_analysis",
                output_format="text",
                temperature=0.1,
            ),
            "qwen2.5:1.5b",
            "  Thank you for reaching out. Happy to schedule a brief call next week.  ",
            "Thank you for reaching out. Happy to schedule a brief call next week.",
        ),
        # EmailGenerator initial outreach profile (temperature=0.2, num_ctx=4096, text / raw_text preserves fence)
        (
            SalesLLMRequest(
                user_prompt="Generate outreach JSON",
                task="sales_outreach",
                output_format="text",
                model="qwen2.5:1.5b",
                temperature=0.2,
                num_ctx=4096,
            ),
            "qwen2.5:1.5b",
            '```json\n{"subject": "Revit Automation", "body": "Hi team"}\n```',
            '```json\n{"subject": "Revit Automation", "body": "Hi team"}\n```',
        ),
        # EmailGenerator JSON repair profile (temperature=0.0, num_ctx=4096, text)
        (
            SalesLLMRequest(
                user_prompt="Repair invalid JSON",
                task="sales_outreach",
                output_format="text",
                model="qwen2.5:1.5b",
                temperature=0.0,
                num_ctx=4096,
            ),
            "qwen2.5:1.5b",
            '{"subject": "Revit Automation", "body": "Hi team"}',
            '{"subject": "Revit Automation", "body": "Hi team"}',
        ),
    ]

    for native_req, model_name, raw_reply, expected_content in cases:
        native_sync = MagicMock()
        native_sync.chat.return_value = _make_provider_response(raw_reply)
        native_client = SalesLLMClient(default_model=model_name, sync_client=native_sync)

        native_resp = native_client.generate(native_req, model_name=model_name)

        assert native_sync.chat.call_args.kwargs == expected_ollama_chat_payload(
            user_prompt=native_req.user_prompt,
            task=native_req.task,
            model=model_name,
            context=native_req.context,
            temperature=native_req.temperature,
            num_ctx=native_req.num_ctx,
        )
        assert native_resp.raw_text == raw_reply
        assert native_resp.content == expected_content
        assert native_resp.model == model_name
