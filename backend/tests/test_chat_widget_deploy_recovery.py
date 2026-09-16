"""
Chat-widget deployment recovery gap: message_processor.py (email channel)
sets tickets.status='processing' before calling the agent, which is what
makes find_and_recover_stale_tickets() (the existing watchdog) able to see
and recover a ticket whose worker died mid-generation (deploy/crash/OOM -
never a catchable Python exception). v2_chat_widget.py never set that flag
at all, so a chat ticket killed mid-generation was invisible to the same
watchdog and would strand silently forever.

Fix: _generate_reply now writes status='processing' immediately before
calling the agent (mirrors message_processor.py's identical pre-call
write), and guarantees every reachable completion path (success with an
explicit outcome, success with no explicit outcome, or a caught agent
exception) writes a real terminal status afterward - never leaves
'processing' as the resting state on a normal return. The watchdog itself,
its 5-minute threshold, and the email path are untouched.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def _ticket(**overrides):
    t = {"id": "ticket-1", "status": "open", "gmail_thread_id": "sess-1", "channel": "chat",
         "messages": [], "customer_email": "c@example.com", "brand_id": "brand-1"}
    t.update(overrides)
    return t


def _agent_result(**overrides):
    r = {
        "reply_body": "Your order shipped yesterday!",
        "confidence_score": 91, "intent": "order_status_inquiry", "status": "auto_resolved",
        "escalate": False, "order_data": None, "action_taken": None,
        "ai_reply_generated": True, "model_used": "test-model",
    }
    r.update(overrides)
    return r


def _post(existing_ticket, agent_mock):
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND_ROW]
        if table == "tickets":
            return [existing_ticket] if existing_ticket else []
        return []

    mock_update = MagicMock()
    patches = [
        patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select),
        patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=lambda t, d: {**d, "id": "ticket-1"}),
        patch("src.api.routes.v2_chat_widget.supabase_update", new=mock_update),
        patch("src.services.auth_service.auth_service.check_daily_ticket_limit", new=AsyncMock(return_value=True)),
        patch("src.services.plan_service.supabase_select", return_value=[TENANT]),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.services.supabase_service.supabase_service.check_conversation_override", new=AsyncMock(return_value=False)),
        patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", new=agent_mock),
    ]
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/v2/widget/chat", json={"brand_id": "brand-1", "session_id": "sess-1", "message": "where is my order?"})
    finally:
        for p in patches:
            p.stop()
    return resp, mock_update


def _ticket_update_calls(mock_update):
    """Every supabase_update("tickets", ...) call, in order, as (match, data)."""
    return [c.args[1:] for c in mock_update.call_args_list if c.args[0] == "tickets"]


# 1. The ticket enters 'processing' before the agent is ever called.
def test_ticket_enters_processing_before_ai_generation():
    captured = {}

    async def fake_agent(*args, **kwargs):
        # At the moment the agent runs, the FIRST ticket write must
        # already have committed status='processing' - this is the
        # write a deploy/crash mid-generation would leave as the last
        # known DB state. Read the patch target directly - the outer
        # `mock_update` local isn't bound yet at this point (_post()
        # hasn't returned), but the module's patched attribute already is.
        captured["first_call"] = v2_chat_widget.supabase_update.call_args_list[0]
        return _agent_result()

    agent_mock = AsyncMock(side_effect=fake_agent)
    resp, mock_update = _post(_ticket(), agent_mock)

    assert resp.status_code == 200
    first_match, first_data = captured["first_call"].args[1], captured["first_call"].args[2]
    assert first_match == {"id": "eq.ticket-1"}
    assert first_data == {"status": "processing"}


# 2. Successful generation reaches the existing normal final state.
def test_successful_generation_reaches_normal_final_state():
    agent_mock = AsyncMock(return_value=_agent_result(status="auto_resolved", escalate=False))
    resp, mock_update = _post(_ticket(), agent_mock)

    assert resp.status_code == 200
    calls = _ticket_update_calls(mock_update)
    assert calls[0][1] == {"status": "processing"}
    final_status = calls[-1][1].get("status")
    assert final_status == "auto_resolved"
    assert final_status != "processing"


# 3. An exception during generation does not leave the ticket in an
#    invalid (stuck-processing) state - existing escalate-on-error
#    behavior is preserved.
def test_exception_does_not_leave_ticket_at_processing():
    agent_mock = AsyncMock(side_effect=RuntimeError("boom"))
    resp, mock_update = _post(_ticket(), agent_mock)

    assert resp.status_code == 200
    calls = _ticket_update_calls(mock_update)
    assert calls[0][1] == {"status": "processing"}
    final_data = calls[-1][1]
    assert final_data.get("status") == "escalated"
    assert final_data.get("escalate") is True


# 4. An outcome the existing status-mapping doesn't explicitly branch on
#    (agent_status not in auto_resolved/auto_resolved_review/escalated -
#    e.g. "ai_suggested") must still exit 'processing', not silently
#    strand there just because ticket_status_update was never set.
def test_unmapped_agent_status_still_exits_processing():
    agent_mock = AsyncMock(return_value=_agent_result(status="ai_suggested", escalate=False))
    resp, mock_update = _post(_ticket(status="open"), agent_mock)

    assert resp.status_code == 200
    calls = _ticket_update_calls(mock_update)
    final_status = calls[-1][1].get("status")
    assert final_status is not None
    assert final_status != "processing"
    # Same effective behavior "leave status untouched" always had for this
    # case - restores the ticket's own pre-call status.
    assert final_status == "open"


# 5. A worker killed mid-generation never reaches its own except block -
#    asyncio.CancelledError (what a graceful-shutdown task cancellation
#    raises, and the closest in-process proxy for "the interpreter is
#    going down") is a BaseException, not an Exception, so the existing
#    `except Exception as e:` does NOT catch it - it propagates straight
#    out, exactly like a real SIGKILL leaving nothing after the pre-call
#    write to ever run. Proves the ticket is left at exactly 'processing'
#    (never touched again), which is the same state message_processor.py's
#    watchdog already knows how to reclaim.
@pytest.mark.asyncio
async def test_interrupted_worker_leaves_status_processing_for_watchdog():
    mock_update = MagicMock()
    agent_mock = AsyncMock(side_effect=asyncio.CancelledError())
    body = v2_chat_widget.ChatRequest(brand_id="brand-1", session_id="sess-1", message="hi")

    with patch("src.api.routes.v2_chat_widget.supabase_update", new=mock_update), \
         patch("src.agent.customer_success_agent.customer_success_agent.process_customer_query", new=agent_mock):
        with pytest.raises(asyncio.CancelledError):
            await v2_chat_widget._generate_reply(
                body=body, brand=BRAND_ROW, ticket=_ticket(), ticket_id="ticket-1", tenant_id="tenant-1",
            )

    calls = _ticket_update_calls(mock_update)
    assert len(calls) == 1
    assert calls[0][1] == {"status": "processing"}
