import os
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.inbox.store import InboxStore
from sales_engine.inbox.models import Conversation, InboundMessage, ReplySuggestion
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany


@pytest.fixture
def client_env(tmp_path):
    db_file = tmp_path / "test_api_ui.db"
    os.environ["SALES_OUTREACH_DB"] = str(db_file)
    os.environ["SMTP_FROM_EMAIL"] = "outreach@pybim.it"
    os.environ["EMAIL_SEND_ENABLED"] = "true"

    cand_store = CandidateStore(db_path=str(db_file))
    inbox_store = InboxStore(db_path=db_file)

    # Seed a candidate company
    cand = CandidateCompany(
        candidate_id="cand_test_ui",
        company_name="Studio Test BIM",
        company_domain="studiotest.it",
    )
    cand_store.save_candidate(cand)

    # Seed a conversation
    conv = Conversation(
        conversation_id="conv_api_1",
        company_id="cand_test_ui",
        provider="mock",
        status="waiting_on_us",
        reply_category="positive_interest",
        active_conversation=True,
    )
    inbox_store.save_conversation(conv)

    # Seed an inbound message
    inb = InboundMessage(
        inbound_message_id="inb_api_1",
        provider="mock",
        provider_message_id="pmsg_api_1",
        from_email="director@studiotest.it",
        from_name="Director Rossi",
        subject="Re: pyBIM Automation",
        body_text="We are interested in your Revit automation services. Can we speak Tuesday at 10am?",
        company_id="cand_test_ui",
        conversation_id="conv_api_1",
        classification="positive_interest",
    )
    inbox_store.save_inbound_message(inb)

    # Seed a suggestion
    sug = ReplySuggestion(
        suggestion_id="sug_api_1",
        conversation_id="conv_api_1",
        inbound_message_id="inb_api_1",
        subject="Re: pyBIM Automation",
        body="Thank you for getting back. We would be glad to speak on Tuesday.",
        status="pending_review",
    )
    inbox_store.save_suggestion(sug)

    client = TestClient(app)
    return {
        "client": client,
        "db_file": db_file,
        "inbox_store": inbox_store,
        "cand_store": cand_store,
    }


def test_api_inbox_status(client_env):
    client = client_env["client"]
    res = client.get("/api/sales/inbox/status")
    assert res.status_code == 200
    data = res.json()
    assert data["provider"] in ("mock", "gmail", "graph")
    assert "status" in data


def test_api_inbox_sync(client_env):
    client = client_env["client"]
    res = client.post("/api/sales/inbox/sync")
    assert res.status_code == 200
    data = res.json()
    assert "messages_fetched" in data
    assert "new_replies" in data


def test_api_conversations_list(client_env):
    client = client_env["client"]
    res = client.get("/api/sales/conversations")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert any(c["conversation_id"] == "conv_api_1" for c in data)


def test_api_conversation_detail(client_env):
    client = client_env["client"]
    res = client.get("/api/sales/conversations/conv_api_1")
    assert res.status_code == 200
    data = res.json()
    assert data["conversation"]["conversation_id"] == "conv_api_1"
    assert len(data["messages"]) >= 1
    assert data["latest_suggestion"] is not None


def test_api_reclassify_conversation(client_env):
    client = client_env["client"]
    res = client.post(
        "/api/sales/conversations/conv_api_1/classify",
        json={"category": "question", "reasoning": "Client asked technical question"},
    )
    assert res.status_code == 200
    assert res.json()["new_category"] == "question"


def test_api_suggest_reply(client_env):
    client = client_env["client"]
    res = client.post("/api/sales/conversations/conv_api_1/suggest-reply")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending_review"
    assert len(data["body"]) > 10


def test_api_edit_approve_send_flow(client_env):
    client = client_env["client"]

    # 1. Edit
    res_edit = client.post(
        "/api/sales/reply-suggestions/sug_api_1/edit",
        json={"subject": "Re: Custom Subject", "body": "Custom edited body."},
    )
    assert res_edit.status_code == 200
    assert res_edit.json()["subject"] == "Re: Custom Subject"
    assert res_edit.json()["status"] == "pending_review"

    # 2. Approve
    res_appr = client.post(
        "/api/sales/reply-suggestions/sug_api_1/approve",
        json={"reviewer": "test_operator"},
    )
    assert res_appr.status_code == 200
    assert res_appr.json()["status"] == "approved"

    # 3. Send
    res_send = client.post(
        "/api/sales/reply-suggestions/sug_api_1/send",
        json={
            "sender_email": "outreach@pybim.it",
            "operator_confirmed": True,
        },
    )
    assert res_send.status_code == 200
    assert res_send.json()["status"] == "sent"


def test_ui_replies_list_renders(client_env):
    client = client_env["client"]
    res = client.get("/sales/replies")
    assert res.status_code == 200
    assert "Replies &amp; Inbound Intelligence" in res.text or "Replies & Inbound Intelligence" in res.text
    assert "Studio Test BIM" in res.text
    assert "conv_api_1" in res.text


def test_ui_reply_detail_renders(client_env):
    client = client_env["client"]
    res = client.get("/sales/replies/conv_api_1")
    assert res.status_code == 200
    assert "Conversation Thread" in res.text
    assert "Studio Test BIM" in res.text
    assert "director@studiotest.it" in res.text
    assert "Suggested Operator Reply" in res.text
