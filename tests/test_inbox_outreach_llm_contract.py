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


# ---------------------------------------------------------------------------
# EmailGenerator & Backend Native Injection Boundary
# ---------------------------------------------------------------------------


def _make_outreach_context(*, language: str = "en"):
    from sales_engine.outreach.schemas import OutreachContext, OutreachEvidenceItem, SenderProfile

    return OutreachContext(
        lead_id="domain:acme-bim.example",
        contact_id="email:jane@acme-bim.example",
        company_name="Acme BIM Engineering",
        company_domain="acme-bim.example",
        recipient_name="Jane Doe",
        recipient_title="BIM Manager",
        recipient_email="jane@acme-bim.example",
        active_services=["Tech-Enabled BIM Services"],
        evidence_items=[
            OutreachEvidenceItem(
                id="JOB-001",
                category="job",
                title="Revit API Developer",
                content="Hiring Revit API Developer for custom BIM automation.",
                url="https://acme-bim.example/careers/1",
            ),
            OutreachEvidenceItem(
                id="SERVICE-001",
                category="service",
                title="Tech-Enabled BIM Services",
                content="Custom Revit API automation and BIM engineering support.",
            ),
        ],
        source_job_urls=["https://acme-bim.example/careers/1"],
        language=language,
        tone="professional_concise",
        sender=SenderProfile(
            sender_name="Alex Turner",
            sender_company="pyBIM",
            sender_role="Lead Automation Architect",
            sender_email="alex@pybim.it",
            sender_website="https://pybim.it",
            signature="Best regards,\nAlex Turner\npyBIM",
        ),
    )


def _valid_outreach_json(*, language: str = "en") -> str:
    if language == "it":
        return json.dumps(
            {
                "subject": "Automazione Revit per Acme BIM Engineering",
                "body": (
                    "Gentile Jane,\n\n"
                    "Abbiamo notato la ricerca di un Revit API Developer in Acme BIM Engineering. "
                    "Con pyBIM e i nostri Tech-Enabled BIM Services supportiamo i team BIM nell'automazione.\n\n"
                    "Sarebbe disponibile per una breve call la prossima settimana?"
                ),
                "service_used": "Tech-Enabled BIM Services",
                "personalization_notes": "Segnale assunzione Revit API",
                "evidence_refs": ["JOB-001", "SERVICE-001"],
                "cta": "Breve call la prossima settimana?",
            }
        )
    return json.dumps(
        {
            "subject": "Revit Automation Support for Acme BIM Engineering",
            "body": (
                "Hi Jane,\n\n"
                "I noticed Acme BIM Engineering is hiring a Revit API Developer. "
                "At pyBIM, our Tech-Enabled BIM Services help engineering teams accelerate Revit automation.\n\n"
                "Would you be open to a brief 10-minute chat next week?"
            ),
            "service_used": "Tech-Enabled BIM Services",
            "personalization_notes": "Mapped to Revit API hiring signal",
            "evidence_refs": ["JOB-001", "SERVICE-001"],
            "cta": "Open to a brief chat next week?",
        }
    )


def test_email_generator_native_payload_parity_model_precedence_and_fences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify EmailGenerator model precedence, fenced raw_text parsing, branding, IT/EN, and legacy parity."""
    from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest
    from sales_engine.llm_client import SALES_OUTREACH_SYSTEM_PROMPT
    from sales_engine.outreach.email_generator import EmailGenerator

    # Model precedence: explicit > OUTREACH_LLM_MODEL > SALES_LLM_MODEL > default
    monkeypatch.delenv("OUTREACH_LLM_MODEL", raising=False)
    monkeypatch.delenv("SALES_LLM_MODEL", raising=False)
    assert EmailGenerator(llm_client=SalesLLMClient(sync_client=MagicMock())).model_name == "qwen2.5:1.5b"
    monkeypatch.setenv("SALES_LLM_MODEL", "sales-env-model")
    assert EmailGenerator(llm_client=SalesLLMClient(sync_client=MagicMock())).model_name == "sales-env-model"
    monkeypatch.setenv("OUTREACH_LLM_MODEL", "outreach-env-model")
    assert EmailGenerator(llm_client=SalesLLMClient(sync_client=MagicMock())).model_name == "outreach-env-model"
    assert (
        EmailGenerator(
            llm_client=SalesLLMClient(sync_client=MagicMock()),
            model_name="explicit-override",
        ).model_name
        == "explicit-override"
    )

    fenced_en = f"```json\n{_valid_outreach_json(language='en')}\n```"
    sync_mock = MagicMock()
    sync_mock.chat.return_value = _make_provider_response(fenced_en)
    gen = EmailGenerator(
        llm_client=SalesLLMClient(sync_client=sync_mock),
        model_name="outreach-model",
    )
    ctx_en = _make_outreach_context(language="en")
    draft, err_code, err_msg = gen.generate_draft(ctx_en, revision=1)

    assert err_code is None and err_msg is None
    assert draft is not None
    assert draft.generation_model == "outreach-model"
    assert draft.approval_status == "pending_review"
    assert draft.send_status == "not_sent"
    assert "pyBIM" in draft.body

    native_kwargs = sync_mock.chat.call_args.kwargs
    assert native_kwargs["model"] == "outreach-model"
    assert native_kwargs["options"] == {"temperature": 0.2, "num_ctx": 4096}
    assert native_kwargs["messages"][0]["content"] == SALES_OUTREACH_SYSTEM_PROMPT

    legacy_sync = MagicMock()
    legacy_sync.chat.return_value = _make_provider_response(fenced_en)
    legacy_client = BIMLLMClient.__new__(BIMLLMClient)
    legacy_client.default_model = "qwen2.5-coder:1.5b"
    legacy_client._sync_client = legacy_sync
    legacy_req = CodeGenerationRequest(
        user_prompt=native_kwargs["messages"][1]["content"],
        environment="sales_outreach",
        language="nlp",
        model="outreach-model",
        temperature=0.2,
        num_ctx=4096,
    )
    legacy_client.generate_code(legacy_req, model_name="outreach-model")
    assert native_kwargs == legacy_sync.chat.call_args.kwargs

    # Multilingual Italian draft preservation
    sync_it = MagicMock()
    sync_it.chat.return_value = _make_provider_response(_valid_outreach_json(language="it"))
    gen_it = EmailGenerator(llm_client=SalesLLMClient(sync_client=sync_it), model_name="outreach-model")
    draft_it, err_it, _ = gen_it.generate_draft(_make_outreach_context(language="it"))
    assert err_it is None and draft_it is not None
    assert draft_it.language == "it"
    assert "pyBIM" in draft_it.body


def test_email_generator_repair_payload_parity_and_error_contracts() -> None:
    """Verify repair temp=0.0 payload parity, exhausted repair, timeout, connection, empty/None, validation."""
    from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest
    from sales_engine.outreach.email_generator import EmailGenerator

    ctx = _make_outreach_context()

    # 1. Single repair retry succeeds with exact repair payload parity (temp=0.0, num_ctx=4096)
    repair_sync = MagicMock()
    repair_sync.chat.side_effect = [
        _make_provider_response("broken json { subject:"),
        _make_provider_response(_valid_outreach_json()),
    ]
    gen = EmailGenerator(llm_client=SalesLLMClient(sync_client=repair_sync), model_name="m1")
    draft, err_code, err_msg = gen.generate_draft(ctx)
    assert err_code is None and err_msg is None and draft is not None
    assert repair_sync.chat.call_count == 2
    repair_kwargs = repair_sync.chat.call_args_list[1].kwargs
    assert repair_kwargs["options"] == {"temperature": 0.0, "num_ctx": 4096}

    legacy_sync = MagicMock()
    legacy_sync.chat.return_value = _make_provider_response(_valid_outreach_json())
    legacy_client = BIMLLMClient.__new__(BIMLLMClient)
    legacy_client.default_model = "qwen2.5-coder:1.5b"
    legacy_client._sync_client = legacy_sync
    legacy_client.generate_code(
        CodeGenerationRequest(
            user_prompt=repair_kwargs["messages"][1]["content"],
            environment="sales_outreach",
            language="nlp",
            model="m1",
            temperature=0.0,
            num_ctx=4096,
        ),
        model_name="m1",
    )
    assert repair_kwargs == legacy_sync.chat.call_args.kwargs

    # 2. Exhausted repair (invalid, empty, None) -> parse_failed
    for pair in (
        [_make_provider_response("bad1"), _make_provider_response("bad2")],
        [_make_provider_response(""), _make_provider_response("")],
        [_make_provider_response(None), _make_provider_response(None)],
    ):
        s_bad = MagicMock()
        s_bad.chat.side_effect = pair
        d, code, msg = EmailGenerator(llm_client=SalesLLMClient(sync_client=s_bad)).generate_draft(ctx)
        assert d is None and code == "parse_failed" and msg is not None

    # 3. Repair call exception -> generation_failed
    s_rep_err = MagicMock()
    s_rep_err.chat.side_effect = [_make_provider_response("bad"), RuntimeError("repair boom")]
    d, code, _ = EmailGenerator(llm_client=SalesLLMClient(sync_client=s_rep_err)).generate_draft(ctx)
    assert d is None and code == "generation_failed"

    # 4. Primary TimeoutError -> timeout; ConnectionError -> model_unavailable
    s_to = MagicMock()
    s_to.chat.side_effect = TimeoutError("timed out")
    d, code, _ = EmailGenerator(llm_client=SalesLLMClient(sync_client=s_to)).generate_draft(ctx)
    assert d is None and code == "timeout"

    s_conn = MagicMock()
    s_conn.chat.side_effect = ConnectionError("unreachable")
    d, code, _ = EmailGenerator(llm_client=SalesLLMClient(sync_client=s_conn)).generate_draft(ctx)
    assert d is None and code == "model_unavailable"

    # 5. Validation failure (prohibited/ungrounded service) -> validation_failed
    s_val = MagicMock()
    s_val.chat.return_value = _make_provider_response(
        json.dumps(
            {
                "subject": "Bad",
                "body": "Too short",
                "service_used": "Unapproved Secret Product",
                "personalization_notes": "",
                "evidence_refs": [],
                "cta": "",
            }
        )
    )
    d, code, _ = EmailGenerator(llm_client=SalesLLMClient(sync_client=s_val)).generate_draft(ctx)
    assert d is None and code == "validation_failed"


def test_backend_generate_drafts_injection_boundary_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real `lifespan`, `get_sales_llm_client`, `get_llm_client`, and `generate_email_drafts` from `backend/main.py` AST."""
    import ast
    import asyncio
    from contextlib import asynccontextmanager
    import threading
    from unittest.mock import patch

    main_path = REPO_ROOT / "backend" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    wanted_funcs = {"lifespan", "get_llm_client", "get_sales_llm_client", "generate_email_drafts"}
    selected = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_sales_llm_client_lock"
        ):
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted_funcs:
            # Keep @asynccontextmanager on lifespan; strip FastAPI route decorator on generate_email_drafts
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
    assert any(getattr(n, "name", None) == "lifespan" for n in selected)
    assert any(
        isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "_sales_llm_client_lock"
        for n in selected
    )

    class DummyHTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    # Start with completely uninitialized app.state (missing both llm_client and sales_llm_client)
    fake_app = SimpleNamespace(state=SimpleNamespace())
    fake_bim_cls = MagicMock(return_value="created-legacy-bim")
    ns: dict[str, object] = {
        "app": fake_app,
        "threading": threading,
        "asynccontextmanager": asynccontextmanager,
        "FastAPI": object,
        "BIMLLMClient": fake_bim_cls,
        "HTTPException": DummyHTTPException,
        "GenerateDraftsRequest": object,
    }
    mod = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(mod)
    exec(compile(mod, filename=str(main_path), mode="exec"), ns)

    # 1. Missing/uninitialized app.state lazily initializes SalesLLMClient cleanly without touching legacy client
    monkeypatch.setenv("SALES_LLM_MODEL", "model-cycle-1")
    fake_req = SimpleNamespace(jobs=[])
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"status": "ok", "drafts": []}
    with (
        patch("sales_engine.outreach.email_generator.EmailGenerator") as mock_eg_cls,
        patch("sales_engine.outreach.outreach_orchestrator.OutreachOrchestrator") as mock_orch_cls,
    ):
        mock_orch_cls.return_value.generate_drafts.return_value = fake_result
        out = asyncio.run(ns["generate_email_drafts"](fake_req))
    assert out == {"status": "ok", "drafts": []}
    injected_1 = mock_eg_cls.call_args.kwargs["llm_client"]
    assert isinstance(injected_1, SalesLLMClient)
    assert injected_1.default_model == "model-cycle-1"
    assert injected_1._sync_client is None
    assert not hasattr(fake_app.state, "llm_client")

    # 2. Explicit app.state.sales_llm_client = None re-initializes cleanly; native constructor error raises HTTP 500
    fake_app.state.sales_llm_client = None
    fake_app.state.llm_client = None
    with patch("sales_engine.llm_client.SalesLLMClient", side_effect=RuntimeError("bad host config")):
        with pytest.raises(DummyHTTPException) as exc_info:
            asyncio.run(ns["generate_email_drafts"](fake_req))
    assert exc_info.value.status_code == 500
    assert "Draft generation failed: bad host config" in exc_info.value.detail
    assert fake_app.state.sales_llm_client is None

    # 3. Concurrent first initialization yields identical cached SalesLLMClient instance
    fake_app.state.sales_llm_client = None
    barrier = threading.Barrier(4)
    results: list[object] = []

    def worker() -> None:
        barrier.wait(timeout=5.0)
        results.append(ns["get_sales_llm_client"]())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive()
    assert len(results) == 4 and all(r is results[0] for r in results)

    # 4. Lifespan shutdown clears sales_llm_client and subsequent lifespan picks up updated env config
    async def run_two_lifecycles() -> tuple[str, str]:
        monkeypatch.setenv("SALES_LLM_MODEL", "cycle-one-model")
        async with ns["lifespan"](fake_app):
            first_model = fake_app.state.sales_llm_client.default_model
        assert fake_app.state.sales_llm_client is None

        monkeypatch.setenv("SALES_LLM_MODEL", "cycle-two-model")
        async with ns["lifespan"](fake_app):
            second_model = fake_app.state.sales_llm_client.default_model
        assert fake_app.state.sales_llm_client is None
        return first_model, second_model

    m1, m2 = asyncio.run(run_two_lifecycles())
    assert m1 == "cycle-one-model"
    assert m2 == "cycle-two-model"
    assert ns["get_llm_client"]() == "created-legacy-bim"


def test_email_generator_import_isolation(tmp_path: Path) -> None:
    """Importing EmailGenerator must not load ai_engine, ollama, chromadb, scrapling, or sqlite3."""
    proc = run_isolated_subprocess(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys; "
                "import sales_engine.outreach.email_generator; "
                "loaded = {m: (m in sys.modules) for m in ("
                "'ai_engine', 'ai_engine.llm_client', 'ollama', 'chromadb', "
                "'scrapling', 'langchain_core', 'sqlite3')}; "
                "print(json.dumps(loaded))"
            ),
        ],
        isolated_db_path=tmp_path / "isolated_eg_probe.db",
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
