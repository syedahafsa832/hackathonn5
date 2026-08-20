"""
Human-takeover gate — chat widget entry point (POST /api/v2/widget/chat).

The critical invariant: a customer must never receive an AI response after a
merchant has taken ownership of the conversation. message_processor.py's
email channel already re-checks conversation_overrides fresh, right before
deciding whether to send (see test_human_takeover_persistence.py) - but the
chat widget's chat() had NO equivalent check at all, so a customer typing
into a live chat window during/after a merchant takeover would still get a
real, model-generated reply. Fixed by gating chat() on the same authoritative
signal (conversation_overrides, via supabase_service.check_conversation_override)
plus ticket.status == 'human_managing', checked fresh on every request
(never cached) immediately before generation.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_chat_widget  # noqa: E402

app = FastAPI()
app.include_router(v2_chat_widget.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ROW = {"id": "brand-1", "tenant_id": "tenant-1", "name": "Test Brand", "agent_name": "Luna"}
TENANT = {"id": "tenant-1", "email": "merchant@example.com", "plan": "growth"}


def setup_function():
    v2_chat_widget._rate_buckets.clear()


def _agent_result():
    return {
        "reply_body": "Your order shipped yesterday!",
        "confidence_score": 91, "intent": "order_status_inquiry", "status": "auto_resolved",
        "escalate": False, "order_data": None, "action_taken": None,
        "ai_reply_generated": True, "model_used": "test-model",
    }


def _post(existing_ticket, override_active, message="are you still there?"):
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND_ROW]
        if table == "tickets":
            return [existing_ticket] if existing_ticket else []
        return []

    agent_mock = AsyncMock(return_value=_agent_result())
    patches = [
        patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select),
        patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=lambda t, d: {**d, "id": "ticket-new"}),
        patch("src.api.routes.v2_chat_widget.supabase_update"),
        patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=True)),
        patch("src.services.plan_service.supabase_select", return_value=[TENANT]),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.services.supabase_service.supabase_service.check_conversation_override", new=AsyncMock(return_value=override_active)),
        patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", new=agent_mock),
    ]
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/v2/widget/chat", json={"brand_id": "brand-1", "session_id": "sess-1", "message": message})
    finally:
        for p in patches:
            p.stop()
    return resp, agent_mock


def _streaming_post(existing_ticket, override_active):
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND_ROW]
        if table == "tickets":
            return [existing_ticket] if existing_ticket else []
        return []

    agent_mock = AsyncMock(return_value=_agent_result())
    patches = [
        patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select),
        patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=lambda t, d: {**d, "id": "ticket-new"}),
        patch("src.api.routes.v2_chat_widget.supabase_update"),
        patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=True)),
        patch("src.services.plan_service.supabase_select", return_value=[TENANT]),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.services.supabase_service.supabase_service.check_conversation_override", new=AsyncMock(return_value=override_active)),
        patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", new=agent_mock),
    ]
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/v2/widget/chat?stream=1", json={"brand_id": "brand-1", "session_id": "sess-1", "message": "hello"})
    finally:
        for p in patches:
            p.stop()
    return resp, agent_mock


def _ticket(**overrides):
    t = {"id": "ticket-1", "status": "open", "gmail_thread_id": "sess-1", "channel": "chat",
         "messages": [], "customer_email": "c@example.com", "brand_id": "brand-1"}
    t.update(overrides)
    return t


# ── No override: unaffected, agent still called (no regression) ────────────

def test_no_takeover_still_calls_the_agent():
    resp, agent_mock = _post(existing_ticket=None, override_active=False)
    agent_mock.assert_awaited_once()
    assert resp.json()["reply"] == "Your order shipped yesterday!"


# ── The bug this fix closes ─────────────────────────────────────────────────

def test_ticket_status_human_managing_blocks_ai_reply():
    resp, agent_mock = _post(existing_ticket=_ticket(status="human_managing"), override_active=False)
    agent_mock.assert_not_called()
    assert resp.status_code == 200
    body = resp.json()
    assert "team" in body["reply"].lower()
    assert body["reply"] != "Your order shipped yesterday!"


def test_active_override_blocks_ai_reply_even_when_status_was_reset_to_resolved():
    """Same class of bug as test_human_takeover_persistence.py's ticket
    #4a641e86: a manual reply can legitimately flip tickets.status back to
    'resolved' while conversation_overrides.active stays True. The chat
    widget must key off the override, not status alone."""
    resp, agent_mock = _post(existing_ticket=_ticket(status="resolved"), override_active=True)
    agent_mock.assert_not_called()
    assert "team" in resp.json()["reply"].lower()


def test_streaming_request_is_also_gated_by_takeover():
    resp, agent_mock = _streaming_post(existing_ticket=_ticket(status="human_managing"), override_active=False)
    agent_mock.assert_not_called()
    assert "ndjson" not in resp.headers.get("content-type", "")
    assert "team" in resp.json()["reply"].lower()


def test_takeover_check_db_error_fails_closed_not_open():
    """If the override lookup itself errors (DB blip), never gamble the
    conversation on the AI - block this turn rather than risk an AI reply
    landing on a ticket a human may already own."""
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND_ROW]
        if table == "tickets":
            return [_ticket(status="open")]
        return []

    agent_mock = AsyncMock(return_value=_agent_result())
    patches = [
        patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select),
        patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=lambda t, d: {**d, "id": "ticket-new"}),
        patch("src.api.routes.v2_chat_widget.supabase_update"),
        patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=True)),
        patch("src.services.plan_service.supabase_select", return_value=[TENANT]),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.services.supabase_service.supabase_service.check_conversation_override", new=AsyncMock(side_effect=Exception("db down"))),
        patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", new=agent_mock),
    ]
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/v2/widget/chat", json={"brand_id": "brand-1", "session_id": "sess-1", "message": "hi"})
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 200
    agent_mock.assert_not_called()
    assert "team" in resp.json()["reply"].lower()
