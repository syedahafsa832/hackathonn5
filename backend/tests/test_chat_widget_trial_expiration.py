"""
Free-trial AI entitlement enforcement — chat widget entry point.

The chat widget (POST /api/v2/widget/chat) is the second live path that
calls customer_success_agent.process_customer_query directly (alongside
message_processor.py's Gmail/webform/WhatsApp path — see
test_trial_expiration_ai_gate.py for that one). It shares the exact same
underlying bug: check_limit(tenant_id, "ai_replies") is a *quota* check, and
for a tenant plan_service._resolve_plan() has downgraded to "free" (what an
expired trial or a lapsed paid plan becomes), it still reports "allowed" up
to the Free plan's own daily AI-reply quota — so an expired-trial customer
chatting in could still get a real, model-generated reply.

plan_service.check_ai_entitlement() closes this the same way here as in the
Gmail path: checked immediately after the tenant is resolved from the
brand, before the daily-ticket-limit check, before the AI-reply quota
check, and before the agent is ever called.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
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


def setup_function():
    v2_chat_widget._rate_buckets.clear()


def _fake_supabase_select(table, params=None):
    if table == "brands":
        return [BRAND_ROW]
    if table == "tickets":
        return []
    return []


def _fake_supabase_insert(table, data):
    if table == "tickets":
        return {**data, "id": "ticket-1"}
    return {**data, "id": "generated-id"}


def _active_trial_tenant():
    return {"id": "tenant-1", "email": "merchant@example.com", "plan": "trial",
            "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()}


def _active_paid_tenant():
    return {"id": "tenant-1", "email": "merchant@example.com", "plan": "growth",
            "plan_activated_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()}


def _expired_trial_tenant():
    return {"id": "tenant-1", "email": "merchant@example.com", "plan": "trial",
            "trial_end_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}


def _agent_result():
    return {
        "reply_body": "Your order shipped yesterday!",
        "confidence_score": 91, "intent": "order_status_inquiry", "status": "auto_resolved",
        "escalate": False, "order_data": None, "action_taken": None,
        "ai_reply_generated": True, "model_used": "test-model",
    }


def _common_patches(tenant, ticket_update_side_effect=None):
    return [
        patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=_fake_supabase_select),
        patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=_fake_supabase_insert),
        patch("src.api.routes.v2_chat_widget.supabase_update", side_effect=ticket_update_side_effect),
        patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=True)),
        patch("src.services.plan_service.supabase_select", return_value=[tenant]),
        patch("src.services.plan_service.record_ai_reply_event"),
    ]


def _request_body(message="where is my order #1234?"):
    return {"brand_id": "brand-1", "session_id": "sess-1", "message": message}


def _post(url, tenant, agent_mock, ticket_update_side_effect=None):
    """Starts every patch together, posts to url, then stops the SAME
    patcher objects — calling _common_patches(tenant) a second time for
    teardown would stop patchers that were never started."""
    patches = _common_patches(tenant, ticket_update_side_effect) + [
        patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", new=agent_mock),
    ]
    for p in patches:
        p.start()
    try:
        return client.post(url, json=_request_body())
    finally:
        for p in patches:
            p.stop()


# ── Entitled tenants are unaffected — no regression ─────────────────────────

def test_active_trial_still_calls_the_agent():
    agent_mock = AsyncMock(return_value=_agent_result())
    resp = _post("/api/v2/widget/chat", _active_trial_tenant(), agent_mock)

    agent_mock.assert_awaited_once()
    assert resp.json()["reply"] == "Your order shipped yesterday!"


def test_active_paid_subscription_still_calls_the_agent():
    agent_mock = AsyncMock(return_value=_agent_result())
    resp = _post("/api/v2/widget/chat", _active_paid_tenant(), agent_mock)

    agent_mock.assert_awaited_once()
    assert resp.json()["reply"] == "Your order shipped yesterday!"


# ── Expired trial: the agent is never reached, no AI reply is sent ────────

def test_expired_trial_never_calls_the_agent():
    agent_mock = AsyncMock(return_value=_agent_result())
    resp = _post("/api/v2/widget/chat", _expired_trial_tenant(), agent_mock)

    agent_mock.assert_not_called()
    assert resp.status_code == 200
    body = resp.json()
    assert "trial" in body["reply"].lower()
    assert body["reply"] != "Your order shipped yesterday!"


def test_expired_trial_streaming_request_also_never_calls_the_agent():
    """The chat widget's ?stream=1 activity-status path shares the exact
    same tenant/brand resolution before branching into the streaming
    generator — must be gated identically, not just the plain-JSON path."""
    agent_mock = AsyncMock(return_value=_agent_result())
    resp = _post("/api/v2/widget/chat?stream=1", _expired_trial_tenant(), agent_mock)

    agent_mock.assert_not_called()
    assert "ndjson" not in resp.headers.get("content-type", "")
    assert "trial" in resp.json()["reply"].lower()


def test_expired_trial_ticket_is_escalated_to_human():
    update_calls = []

    def capture_update(table, match, data):
        update_calls.append((table, match, data))

    resp = _post("/api/v2/widget/chat", _expired_trial_tenant(), AsyncMock(), ticket_update_side_effect=capture_update)

    assert resp.status_code == 200
    assert any(d.get("status") == "requires_human" for (_, _, d) in update_calls)
