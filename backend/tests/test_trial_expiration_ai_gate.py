"""
Free-trial AI entitlement enforcement (critical cost-control fix).

Bug found in production: an expired-trial tenant (no active paid plan)
still received a real, model-generated AI email reply. Root cause:
plan_service._resolve_plan() silently downgrades an expired trial (and a
lapsed paid plan) to the "free" plan, and the only gate that used to sit in
front of AI generation - check_limit(tenant_id, "ai_replies") - is a
*quota* check. Against the Free plan's own daily allowance (10/day) it
still answers "allowed" until that's exhausted, so a freshly-expired trial
tenant, having used 0 replies today, sailed straight through to a real LLM
call and a real outbound email.

Fix: plan_service.check_ai_entitlement() is a new, quota-independent gate -
"does this tenant have ANY active AI entitlement right now" (active trial,
active paid plan, or the legacy founding_free grant) - called as early as
possible, right after the tenant is identified and before any other
Supabase/RAG/LLM/Shopify-tool work, in both live AI entry points:
message_processor.py (the real Gmail/webform/WhatsApp/quarantine-release
production path - see its own module docstring/comments for why this is
"the" live path, not brand_message_processor.py) and v2_chat_widget.py.

These tests prove, end-to-end through the real process_message() pipeline:
1. an active trial still gets a real AI reply (no regression)
2. an active paid plan still gets a real AI reply (no regression)
3. an expired trial never reaches the agent at all
4. the actual LLM call and a live Shopify tool call are never reached
   either, even with nothing but the entitlement gate protecting them
5. no automated email is ever sent for a rejected message
6. a thread-continuation reply (the code path a previous bug already lived
   in - duplicate thread handling) cannot bypass the guard
7. the human/manual-reply send path is completely untouched by this change
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def run(coro):
    # See test_chat_widget_action_staging.py's run() for why: an earlier
    # @pytest.mark.asyncio test in the full suite can leave a closed loop as
    # the thread's "current" one.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _message(**overrides):
    base = {
        "channel": "email",
        "content": "where is my order #1234?",
        "customer_email": "customer@example.com",
        "customer_name": "Jane Doe",
        "subject": "Order question",
        "store_id": "brand-1",
        "tenant_id": "tenant-1",  # short-circuits STAGE 2.5's own tenant lookup
    }
    base.update(overrides)
    return base


def _ai_result(**overrides):
    result = {
        "reply_body": "It shipped yesterday!",
        "ai_reply_generated": True,
        "model_used": "mistral-large-latest",
        "ai_usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "latency_ms": 100, "attempts": 1},
        "intent": "order_status_inquiry",
        "sentiment": "neutral",
        "risk_level": "low",
        "confidence_score": 90,
        "escalate": False,
    }
    result.update(overrides)
    return result


def _active_trial_tenant():
    return {"id": "tenant-1", "email": "merchant@example.com", "plan": "trial",
            "trial_end_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()}


def _active_paid_tenant():
    return {"id": "tenant-1", "email": "merchant@example.com", "plan": "starter",
            "plan_activated_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()}


def _expired_trial_tenant():
    return {"id": "tenant-1", "email": "merchant@example.com", "plan": "trial",
            "trial_end_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}


def _base_mocks(tenant, mp_select=None):
    """Everything process_message() touches before/around the entitlement
    gate, with plan_service.supabase_select wired to a single real tenant
    row so check_ai_entitlement's own logic runs for real (not mocked) -
    it's the thing under test in this file."""
    mp_select_patch = (
        patch("src.workers.message_processor.supabase_select", side_effect=mp_select)
        if mp_select else
        patch("src.workers.message_processor.supabase_select", return_value=[])
    )
    return [
        mp_select_patch,
        patch("src.workers.message_processor.supabase_update"),
        patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(return_value={"id": "ticket-1"})),
        patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})),
        patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1"})),
        patch("src.services.plan_service.supabase_select", return_value=[tenant]),
        patch("src.services.plan_service.record_email_processed"),
        patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ticket_created"),
        patch("src.services.plan_service.check_limit", return_value={"allowed": True}),
        patch("src.services.plan_service.record_ai_reply_event"),
        patch("src.workers.message_processor.brand_message_processor._log_conversation", new=AsyncMock()),
    ]


def _run_with_mocks(mocks, message, agent_mock=None, extra_patches=None):
    proc = UnifiedMessageProcessor()
    patches = list(mocks) + list(extra_patches or [])
    if agent_mock is not None:
        patches.append(patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=agent_mock))
    for p in patches:
        p.start()
    try:
        result = run(proc.process_message("email_incoming", message))
    finally:
        for p in patches:
            p.stop()
    return result


# ── 1/2. Entitled tenants are unaffected — no regression ──────────────────

def test_active_trial_still_generates_ai_reply():
    agent_mock = AsyncMock(return_value=_ai_result())
    result = _run_with_mocks(_base_mocks(_active_trial_tenant()), _message(), agent_mock=agent_mock)

    agent_mock.assert_awaited_once()
    assert result["status"] != "trial_expired"


def test_active_paid_subscription_still_generates_ai_reply():
    agent_mock = AsyncMock(return_value=_ai_result())
    result = _run_with_mocks(_base_mocks(_active_paid_tenant()), _message(), agent_mock=agent_mock)

    agent_mock.assert_awaited_once()
    assert result["status"] != "trial_expired"


# ── 3. Expired trial: the agent is never reached ───────────────────────────

def test_expired_trial_does_not_call_the_agent():
    agent_mock = AsyncMock(return_value=_ai_result())
    result = _run_with_mocks(_base_mocks(_expired_trial_tenant()), _message(), agent_mock=agent_mock)

    agent_mock.assert_not_called()
    assert result["status"] == "trial_expired"
    assert result["ticket_id"] == "ticket-1"
    assert result["entitlement"]["reason"] == "trial_expired"


def test_expired_trial_ticket_is_escalated_to_human_with_clear_reason():
    """Ticket data must survive (requirement: existing conversations/data
    stay intact) - only escalated, never deleted or blanked out."""
    update_calls = []

    def capture_update(table, match, data):
        update_calls.append((table, match, data))

    mocks = _base_mocks(_expired_trial_tenant())
    mocks[1] = patch("src.workers.message_processor.supabase_update", side_effect=capture_update)

    _run_with_mocks(mocks, _message(), agent_mock=AsyncMock())

    ticket_updates = [d for (t, m, d) in update_calls if t == "tickets"]
    assert any(u.get("status") == "requires_human" for u in ticket_updates)
    assert any("trial" in (u.get("escalation_reason") or "").lower() for u in ticket_updates)


# ── 4. The real LLM call and a real Shopify tool call are never reached ───

def test_expired_trial_llm_and_shopify_tool_calls_are_never_reached():
    """Strongest proof required by the spec: does NOT mock
    customer_success_agent.generate_channel_appropriate_response at all -
    the real agent function stays fully reachable - but spies on the actual
    LLM call (ai_provider_manager.create_chat_completion) and a real
    Shopify tool call (v3_tools.get_order_status) deep inside it. Both must
    stay uncalled, proving message_processor.py's entitlement gate rejects
    the message before the agent is ever entered - not merely before the
    final email is sent."""
    llm_spy = AsyncMock()
    shopify_spy = AsyncMock()

    mocks = _base_mocks(_expired_trial_tenant())
    extra = [
        patch("src.services.ai_provider_manager.ai_provider_manager.create_chat_completion", new=llm_spy),
        patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True),
        patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=shopify_spy),
    ]

    result = _run_with_mocks(mocks, _message(), extra_patches=extra)

    llm_spy.assert_not_called()
    shopify_spy.assert_not_called()
    assert result["status"] == "trial_expired"


# ── 5. No automated email is ever sent ─────────────────────────────────────

def test_expired_trial_sends_no_automated_email():
    send_spy = AsyncMock()
    mocks = _base_mocks(_expired_trial_tenant())
    extra = [patch("src.workers.message_processor.UnifiedMessageProcessor._send_email_with_logging", new=send_spy)]

    _run_with_mocks(mocks, _message(), agent_mock=AsyncMock(), extra_patches=extra)

    send_spy.assert_not_called()


# ── 6. Thread continuation (prior duplicate-thread-handling bug area) ─────

def test_expired_trial_thread_continuation_cannot_bypass_the_guard():
    """STAGE 1.5's existing-thread continuation path reuses an already-open
    ticket instead of creating a new one - this must not let an expired
    trial's reply slip past the entitlement gate just because it's a reply
    within an existing Gmail thread."""
    existing_ticket = {
        "id": "ticket-existing",
        "messages": [{"from": "customer@example.com", "body": "first message", "direction": "inbound"}],
        "status": "open",
    }

    def fake_mp_select(table, params=None):
        if table == "tickets":
            return [existing_ticket]
        return []

    agent_mock = AsyncMock(return_value=_ai_result())
    result = _run_with_mocks(
        _base_mocks(_expired_trial_tenant(), mp_select=fake_mp_select),
        _message(gmail_thread_id="thread-abc"),
        agent_mock=agent_mock,
    )

    agent_mock.assert_not_called()
    assert result["status"] == "trial_expired"
    assert result["ticket_id"] == "ticket-existing"


# ── 7. Human/manual reply path is untouched ────────────────────────────────

def test_manual_human_reply_is_unaffected_by_the_ai_entitlement_gate():
    """send-reply (tickets.py) never calls the agent or plan_service at
    all - a merchant on an expired trial must still be able to take over a
    conversation and reply manually. Same TestClient technique already
    proven in test_human_takeover_persistence.py's
    test_send_reply_never_touches_conversation_overrides."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
    from src.api.routes.tickets import router as tickets_router

    app = FastAPI()
    app.include_router(tickets_router, prefix="/api")

    def fake_tenant():
        return TenantContext(tenant_id="tenant-1", email="merchant@example.com")

    app.dependency_overrides[get_current_tenant] = fake_tenant
    client = TestClient(app)

    ticket = {
        "id": "ticket-1", "store_id": "brand-1",
        "customer_email": "customer@example.com", "subject": "Order question", "messages": [],
    }

    with patch("src.api.routes.tickets._assert_ticket_access", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[{"id": "brand-1", "gmail_connected": True}]), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=AsyncMock(return_value={"success": True})), \
         patch("src.api.routes.tickets.supabase_update"):
        resp = client.post("/api/tickets/ticket-1/send-reply", json={"body": "Hey, a human here — happy to help!"})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    app.dependency_overrides.clear()
