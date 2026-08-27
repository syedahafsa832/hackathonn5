"""
AI-provider outage (all configured Mistral/Groq keys failing on one
request) must never auto-send Luna's old "I've flagged this for my team"
claim. Default behavior: the customer's message is still saved normally
(ticket creation already happens before the agent runs - see
test_realtime_ticket_events.py::test_ticket_created_before_ai_call,
unaffected by this change), a real escalation is created/retained
(escalate=True, status='escalated', provider_outage=True,
escalation_reason=PROVIDER_OUTAGE_REASON - all already covered by
test_ai_provider_fallback.py), and no customer-facing reply is sent at all
- the conversation just waits for a human.

If the brand has explicitly enabled provider_outage_fallback_enabled, a
fixed, deliberately generic placeholder is sent instead (never claiming a
human is on it beyond what the real escalation already guarantees).

These tests cover the two channel-specific consequences of that change:
1. Email routing (_decide_ticket_routing) - test_escalation_routing.py's
   own test_provider_outage_empty_reply_never_auto_sends covers this.
2. Chat widget (POST /api/v2/widget/chat) - the widget shows whatever
   reply_body comes back live, with NO confidence-based gate of its own
   (unlike email). Covered here.
"""
import os
import sys
from unittest.mock import AsyncMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_chat_widget  # noqa: E402
from src.agent.customer_success_agent import (  # noqa: E402
    customer_success_agent, PROVIDER_OUTAGE_CUSTOMER_MESSAGE,
)
from src.services.ai_provider_manager import AllProvidersFailedError  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

app = FastAPI()
app.include_router(v2_chat_widget.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ROW = {"id": "brand-1", "tenant_id": "tenant-1", "name": "Test Brand", "agent_name": "Luna"}
TENANT = {"id": "tenant-1", "email": "merchant@example.com", "plan": "growth"}


def setup_function():
    v2_chat_widget._rate_buckets.clear()


def _outage_result(reply_body=""):
    return {
        "reply_body": reply_body,
        "confidence_score": 40, "intent": "general_inquiry", "status": "escalated",
        "escalate": True, "escalation_reason": "AI reply limit reached...",
        "provider_outage": True, "order_data": None, "action_taken": None,
        "ai_reply_generated": False, "model_used": None,
    }


def _post(agent_result, existing_ticket=None, message="where is my order?"):
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND_ROW]
        if table == "tickets":
            return [existing_ticket] if existing_ticket else []
        return []

    captured_update = {}

    def fake_update(table, match, data):
        if table == "tickets":
            captured_update.update(data)
        return {}

    agent_mock = AsyncMock(return_value=agent_result)
    patches = [
        patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select),
        patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=lambda t, d: {**d, "id": "ticket-new"}),
        patch("src.api.routes.v2_chat_widget.supabase_update", side_effect=fake_update),
        patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=True)),
        patch("src.services.plan_service.supabase_select", return_value=[TENANT]),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.services.supabase_service.supabase_service.check_conversation_override", new=AsyncMock(return_value=False)),
        patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", new=agent_mock),
    ]
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/v2/widget/chat", json={"brand_id": "brand-1", "session_id": "sess-1", "message": message})
    finally:
        for p in patches:
            p.stop()
    return resp, captured_update


def test_provider_outage_default_never_sends_a_fabricated_chat_reply():
    """Default (no customer-facing fallback opt-in): the widget's own
    generic client-side placeholder takes over (data.reply is falsy), never
    a backend-fabricated "I've flagged this" claim - and no blank AI bubble
    gets persisted into the ticket's own message history."""
    resp, ticket_update = _post(_outage_result(reply_body=""))

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == ""  # widget frontend's own `data.reply || ... ` fallback takes over

    # No fake/blank "AI" turn added to the transcript.
    ai_turns = [m for m in ticket_update.get("messages", []) if m.get("role") == "ai"]
    assert ai_turns == []
    # Never claims a reply was actually sent to the customer.
    assert ticket_update.get("email_sent") is False
    # But the escalation is real and persisted.
    assert ticket_update.get("status") == "escalated"
    assert ticket_update.get("escalate") is True
    assert "escalation_reason" in ticket_update


def test_provider_outage_with_fallback_enabled_sends_the_generic_message():
    """When the brand has opted in, the fixed generic placeholder is what
    reaches the customer - a real message, not an empty one."""
    reply_body = f"{PROVIDER_OUTAGE_CUSTOMER_MESSAGE}\n\n- Luna\nTest Brand"
    resp, ticket_update = _post(_outage_result(reply_body=reply_body))

    assert resp.status_code == 200
    body = resp.json()
    assert "reviewing it now" in body["reply"]

    ai_turns = [m for m in ticket_update.get("messages", []) if m.get("role") == "ai"]
    assert len(ai_turns) == 1
    assert "reviewing it now" in ai_turns[0]["body"]
    assert ticket_update.get("email_sent") is True
    assert ticket_update.get("status") == "escalated"


def _run_with_provider_outage(brand_overrides=None):
    """Runs the real process_customer_query end-to-end with every AI
    provider failing, and a real brand row resolved (so
    provider_outage_fallback_enabled is actually read from it, not a
    default)."""
    brand = {"id": "brand-1", "name": "Test Brand", "agent_name": "Luna", **(brand_overrides or {})}

    async def _capture_completion(*args, **kwargs):
        raise AllProvidersFailedError([{"label": "primary", "reason": "rate_limited"}])

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(side_effect=_capture_completion)), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.lib.supabase_client.supabase_select", return_value=[brand]):
        return run(customer_success_agent.process_customer_query(
            query="where is my order?",
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))


def test_process_customer_query_default_brand_produces_no_customer_facing_reply():
    """End-to-end through the real agent function: a brand with no
    provider_outage_fallback_enabled value set (the real-world default for
    every brand today, since the column defaults to false) gets an empty
    reply_body when every provider fails."""
    result = _run_with_provider_outage()
    assert result["reply_body"] == ""
    assert result["escalate"] is True
    assert result["provider_outage"] is True


def test_process_customer_query_reads_the_brands_own_opt_in():
    """A brand that explicitly enabled the setting gets the generic
    placeholder - proving the flag is actually read from the resolved
    brand row, not hardcoded either way."""
    result = _run_with_provider_outage({"provider_outage_fallback_enabled": True})
    assert "reviewing it now" in result["reply_body"]
    assert result["escalate"] is True


def test_normal_successful_reply_is_unaffected_by_this_change():
    """Regression guard: a genuine, successfully-generated reply still
    sends and persists exactly as before - this change only touches the
    provider-outage path."""
    result = {
        "reply_body": "Your order shipped yesterday!",
        "confidence_score": 91, "intent": "order_status_inquiry", "status": "auto_resolved",
        "escalate": False, "order_data": None, "action_taken": None,
        "ai_reply_generated": True, "model_used": "test-model",
    }
    resp, ticket_update = _post(result)

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Your order shipped yesterday!"
    ai_turns = [m for m in ticket_update.get("messages", []) if m.get("role") == "ai"]
    assert len(ai_turns) == 1
    assert ticket_update.get("email_sent") is True
