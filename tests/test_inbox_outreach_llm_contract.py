"""
tests/test_inbox_outreach_llm_contract.py
Isolated unit, synchronous payload-parity, fallback, and import-boundary tests for
migrated `ReplyClassifier` and `ReplySuggestionEngine` (and sync outreach readiness).

Zero operational DB access, zero live Ollama/SMTP/IMAP/network calls, zero FastAPI startup.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sales_engine.inbox.classifier import ReplyClassifier
from sales_engine.inbox.models import Conversation, InboundMessage
from sales_engine.inbox.suggestion import ReplySuggestionEngine
from sales_engine.llm_client import SALES_ANALYSIS_SYSTEM_PROMPT, SalesLLMClient
from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_provider_response(text: str | None) -> SimpleNamespace:
    return SimpleNamespace(message=SimpleNamespace(content=text))


def _make_inbound(
    *,
    subject: str = "Re: BIM Coordination",
    body_text: str = "Grazie per il messaggio, siamo interessati.",
    classification: str = "positive_interest",
) -> tuple[Conversation, InboundMessage]:
    conv = Conversation(
        conversation_id="conv_syn_1",
        candidate_id="cand_syn_1",
        contact_email="contact@example.com",
    )
    msg = InboundMessage(
        inbound_message_id="inb_syn_1",
        provider_message_id="prov_syn_1",
        conversation_id=conv.conversation_id,
        from_email="contact@example.com",
        from_name="Marco Rossi",
        to_emails=["team@pybim.it"],
        subject=subject,
        body_text=body_text,
        classification=classification,  # type: ignore[arg-type]
        received_at="2026-11-24T12:00:00Z",
    )
    return conv, msg


# ---------------------------------------------------------------------------
# ReplyClassifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_email,subject,body,headers,expected_cat",
    [
        ("lead@example.com", "Re: BIM", "Yes, we are interested — happy to discuss.", None, "positive_interest"),
        ("lead@example.com", "Re: BIM", "No thank you, we are not interested.", None, "not_interested"),
        ("lead@example.com", "Re: BIM", "Please unsubscribe me immediately.", None, "unsubscribe"),
        ("mailer-daemon@mail.example.com", "Delivery Failure", "550 5.1.1 user unknown", None, "bounce"),
        ("lead@example.com", "Out of Office", "I am away until Monday.", None, "auto_reply"),
        ("lead@example.com", "Re: BIM", "Let's schedule a quick call Thursday.", None, "meeting_proposed"),
        ("lead@example.com", "Re: BIM", "Please contact my colleague Luca.", None, "referral"),
        ("lead@example.com", "Re: BIM", "Not right now, check back in Q3.", None, "not_now"),
        ("lead@example.com", "Re: BIM", "How much do your BIM plugins cost?", None, "question"),
    ],
)
def test_reply_classifier_deterministic_rules_skip_llm_calls(
    from_email: str,
    subject: str,
    body: str,
    headers: dict[str, str] | None,
    expected_cat: str,
) -> None:
    """Deterministic categories match cleanly and never invoke the injected LLM provider."""
    sync_mock = MagicMock()
    client = SalesLLMClient(sync_client=sync_mock)
    classifier = ReplyClassifier(llm_client=client)

    res = classifier.classify(from_email=from_email, subject=subject, body_text=body, headers=headers)
    assert res.category == expected_cat
    assert res.method == "deterministic"
    sync_mock.list.assert_not_called()
    sync_mock.chat.assert_not_called()


def test_reply_classifier_ambiguous_uses_native_sync_payload_and_zero_temp() -> None:
    """Ambiguous reply checks is_healthy() and sends exact temperature=0.0 JSON payload."""
    from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

    raw_json = '{"category": "positive_interest", "confidence": 0.88, "reasoning": "expressed subtle interest"}'
    sync_mock = MagicMock()
    sync_mock.list.return_value = SimpleNamespace(models=[])
    sync_mock.chat.return_value = _make_provider_response(f"```json\n{raw_json}\n```")

    client = SalesLLMClient(default_model="qwen2.5:1.5b", sync_client=sync_mock)
    classifier = ReplyClassifier(llm_client=client)

    ambiguous_body = "We received your note and forwarded it internally for evaluation next month."
    res = classifier.classify(
        from_email="lead@example.com",
        subject="Re: Follow up",
        body_text=ambiguous_body,
    )

    assert res.category == "positive_interest"
    assert res.method == "llm"
    assert res.confidence == 0.88
    sync_mock.list.assert_called_once()
    sync_mock.chat.assert_called_once()

    native_kwargs = sync_mock.chat.call_args.kwargs
    assert native_kwargs["model"] == "qwen2.5:1.5b"
    assert native_kwargs["options"] == {"temperature": 0.0, "num_ctx": 4096}
    assert [m["role"] for m in native_kwargs["messages"]] == ["system", "user"]
    assert native_kwargs["messages"][0]["content"] == SALES_ANALYSIS_SYSTEM_PROMPT
    assert "<UNTRUSTED_INBOUND_EMAIL>" in native_kwargs["messages"][1]["content"]

    # Parity check against legacy BIMLLMClient
    legacy_sync = MagicMock()
    legacy_sync.chat.return_value = _make_provider_response(raw_json)
    legacy_client = BIMLLMClient.__new__(BIMLLMClient)
    legacy_client.default_model = "qwen2.5:1.5b"
    legacy_client._sync_client = legacy_sync
    legacy_req = CodeGenerationRequest(
        user_prompt=native_kwargs["messages"][1]["content"],
        environment="sales",
        language="json",
        temperature=0.0,
    )
    legacy_client.generate_code(legacy_req)
    assert native_kwargs == legacy_sync.chat.call_args.kwargs


@pytest.mark.parametrize(
    "healthy,chat_side_effect",
    [
        (False, None),
        (True, [RuntimeError("ollama failure")]),
        (True, [_make_provider_response("not valid json")]),
        (True, [_make_provider_response("")]),
        (True, [_make_provider_response(None)]),
    ],
)
def test_reply_classifier_fallbacks_to_unknown_on_provider_errors(
    healthy: bool,
    chat_side_effect: list[object] | None,
) -> None:
    """Unavailable provider, exception, invalid JSON, or empty/None reply falls back to unknown."""
    sync_mock = MagicMock()
    if not healthy:
        sync_mock.list.side_effect = ConnectionError("offline")
    else:
        sync_mock.list.return_value = SimpleNamespace(models=[])
        sync_mock.chat.side_effect = chat_side_effect

    classifier = ReplyClassifier(llm_client=SalesLLMClient(sync_client=sync_mock))
    res = classifier.classify(
        from_email="lead@example.com",
        subject="Re: Follow up",
        body_text="Received, will review internally.",
    )
    assert res.category == "unknown"
    assert res.method == "fallback"
    assert res.confidence == 0.2
    if not healthy:
        sync_mock.chat.assert_not_called()


# ---------------------------------------------------------------------------
# ReplySuggestionEngine
# ---------------------------------------------------------------------------


def test_reply_suggestion_deterministic_italian_and_english_pybim_branding() -> None:
    """Deterministic IT and EN suggestions preserve templates, pyBIM branding, and pending_review."""
    engine = ReplySuggestionEngine()

    conv_it, msg_it = _make_inbound(
        subject="Automazione BIM",
        body_text="Ciao, grazie per aver scritto. Siamo interessati.",
        classification="positive_interest",
    )
    sug_it = engine.generate_suggestion(conv_it, msg_it, contact_name="Marco Rossi")
    assert sug_it is not None
    assert sug_it.status == "pending_review"
    assert sug_it.language == "it"
    assert sug_it.generation_model == "deterministic_rules"
    assert sug_it.subject == "Re: Automazione BIM"
    assert "Team pyBIM" in sug_it.body

    conv_en, msg_en = _make_inbound(
        subject="Re: Revit Plugins",
        body_text="Thanks for reaching out, sounds interesting.",
        classification="positive_interest",
    )
    sug_en = engine.generate_suggestion(conv_en, msg_en, contact_name="Sarah Jones")
    assert sug_en is not None
    assert sug_en.status == "pending_review"
    assert sug_en.language == "en"
    assert sug_en.subject == "Re: Revit Plugins"
    assert "pyBIM Team" in sug_en.body


def test_reply_suggestion_llm_success_payload_parity_and_fallbacks() -> None:
    """Verify native text-output LLM suggestion payload parity and fallbacks on errors/None."""
    from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

    conv, msg = _make_inbound(
        subject="Revit API question",
        body_text="Hello, could we discuss your custom Revit plugins next week?",
        classification="question",
    )

    llm_reply = "Hi Marco, thank you for your question. We would be glad to arrange a brief call next week."
    sync_mock = MagicMock()
    sync_mock.list.return_value = SimpleNamespace(models=[])
    sync_mock.chat.return_value = _make_provider_response(f"  {llm_reply}  ")

    engine = ReplySuggestionEngine(llm_client=SalesLLMClient(default_model="qwen2.5:1.5b", sync_client=sync_mock))
    sug = engine.generate_suggestion(conv, msg, company_name="Acme BIM", contact_name="Marco")
    assert sug is not None
    assert sug.body == llm_reply
    assert sug.status == "pending_review"
    assert sug.generation_model == "qwen2.5:1.5b"

    native_kwargs = sync_mock.chat.call_args.kwargs
    assert native_kwargs["options"] == {"temperature": 0.1, "num_ctx": 4096}
    assert native_kwargs["messages"][0]["content"] == SALES_ANALYSIS_SYSTEM_PROMPT

    legacy_sync = MagicMock()
    legacy_sync.chat.return_value = _make_provider_response(llm_reply)
    legacy_client = BIMLLMClient.__new__(BIMLLMClient)
    legacy_client.default_model = "qwen2.5:1.5b"
    legacy_client._sync_client = legacy_sync
    legacy_req = CodeGenerationRequest(
        user_prompt=native_kwargs["messages"][1]["content"],
        environment="sales",
        language="text",
        temperature=0.1,
    )
    legacy_client.generate_code(legacy_req)
    assert native_kwargs == legacy_sync.chat.call_args.kwargs

    # Fallback scenarios: offline, provider exception, empty, None
    for healthy, effect in (
        (False, None),
        (True, [RuntimeError("boom")]),
        (True, [_make_provider_response("")]),
        (True, [_make_provider_response(None)]),
    ):
        fb_sync = MagicMock()
        if not healthy:
            fb_sync.list.side_effect = ConnectionError("down")
        else:
            fb_sync.list.return_value = SimpleNamespace(models=[])
            fb_sync.chat.side_effect = effect
        fb_engine = ReplySuggestionEngine(llm_client=SalesLLMClient(sync_client=fb_sync))
        fb_sug = fb_engine.generate_suggestion(conv, msg, contact_name="Marco")
        assert fb_sug is not None
        assert "pyBIM Team" in fb_sug.body
        assert fb_sug.status == "pending_review"


def test_inbox_consumers_import_isolation(tmp_path: Path) -> None:
    """Importing ReplyClassifier & ReplySuggestionEngine must not load ai_engine, ollama, chromadb, scrapling, or sqlite3."""
    proc = run_isolated_subprocess(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys; "
                "import sales_engine.inbox.classifier; "
                "import sales_engine.inbox.suggestion; "
                "loaded = {m: (m in sys.modules) for m in ("
                "'ai_engine', 'ai_engine.llm_client', 'ollama', 'chromadb', "
                "'scrapling', 'langchain_core', 'sqlite3')}; "
                "print(json.dumps(loaded))"
            ),
        ],
        isolated_db_path=tmp_path / "isolated_inbox_probe.db",
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
