"""
Two follow-up production bugs.

1. Multi-turn action continuity: "yes, cancel it" (or "go ahead"/"do it")
   names no order number of its own. return_actions_integration.py's
   _extract_order_info() had no fallback to this ticket's own conversation
   state, so intent_result.order_id/regex/orders_by_email all come up
   empty and the flow asked the customer to repeat their order number -
   even though customer_success_agent.py's earlier identity-verification
   fix already reliably preserves it in tickets.detected_order_id (see
   message_processor.py STAGE 9's preserve-by-omission fix). Fix: last-
   resort fallback to that same field, only when every other source is
   empty - a fresh message naming its own order number is never overridden.

2. Resolved-ticket reopening didn't actually work in practice: STAGE 1.5's
   reopen logic (status in ("closed","resolved") -> "open") was already
   correct, but tickets.py's send_reply() (used when a human approves/
   sends a reply, which is what sets status="resolved") sent via Gmail
   with no threadId - the same bug already fixed for the AI auto-reply
   path in message_processor.py, just at a second call site. Without
   threading, the customer's next Gmail reply landed in a thread the
   poller never recorded, so the reopen logic was never reached at all.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── 1. Multi-turn action continuity ─────────────────────────────────────────

def _eligible_unfulfilled():
    return {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled"},
        "items": [{"title": "Essential Hoodie", "variant_title": "M", "price": "45.00"}],
        "order_total": "45.00",
    }


def _run_cancel(query, ticket_id=None, ticket_rows=None, order_id_from_intent=None, email="customer@example.com"):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id=order_id_from_intent, raw_address=None, confidence=0.9)

    def fake_select(table, params=None):
        if table == "tickets":
            return ticket_rows or []
        return []

    with patch("src.services.return_actions_integration.supabase_select", side_effect=fake_select), \
         patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=_eligible_unfulfilled())) as mock_elig, \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": email},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id=ticket_id, intent_result=intent,
        ))
    return result, mock_elig, mock_create


def test_cancel_it_resolves_to_previously_detected_order_from_ticket_state():
    """The exact reported bug: 'yes, cancel it' with no order number of its
    own must resolve against the ticket's own conversation state."""
    result, mock_elig, mock_create = _run_cancel(
        "yes, cancel it", ticket_id="ticket-1",
        ticket_rows=[{"id": "ticket-1", "detected_order_id": "1234"}],
    )
    mock_elig.assert_awaited_once_with("1234", "customer@example.com", tenant_id="tenant-1", brand_id="brand-1")
    assert "ACTION REQUIRED: Ask the customer for their order number" not in result["action_context"]


def test_go_ahead_and_do_it_also_resolve_via_ticket_state():
    for phrasing in ["go ahead", "do it", "yes please", "that's the one", "cancel that"]:
        result, mock_elig, _ = _run_cancel(
            phrasing, ticket_id="ticket-1",
            ticket_rows=[{"id": "ticket-1", "detected_order_id": "1234"}],
        )
        mock_elig.assert_awaited_once_with("1234", "customer@example.com", tenant_id="tenant-1", brand_id="brand-1")


def test_no_ticket_state_and_no_order_number_still_asks_for_clarification():
    """Safety: an ambiguous follow-up with nothing to resolve against must
    still ask, never guess."""
    result, mock_elig, mock_create = _run_cancel(
        "yes, cancel it", ticket_id="ticket-1", ticket_rows=[{"id": "ticket-1", "detected_order_id": None}],
    )
    mock_elig.assert_not_awaited()
    mock_create.assert_not_awaited()
    assert "ACTION REQUIRED" in result["action_context"]


def test_fresh_order_number_in_message_is_never_overridden_by_stale_ticket_state():
    """Regression guard: a genuinely new request naming its own order must
    win over old conversation state, not be silently replaced by it."""
    result, mock_elig, _ = _run_cancel(
        "actually please cancel order #9999", ticket_id="ticket-1",
        ticket_rows=[{"id": "ticket-1", "detected_order_id": "1234"}],
        order_id_from_intent=None,
    )
    mock_elig.assert_awaited_once_with("9999", "customer@example.com", tenant_id="tenant-1", brand_id="brand-1")


def test_non_cancellation_followup_also_uses_ticket_state():
    """_extract_order_info is shared by every action type - proves the
    fallback isn't cancellation-specific."""
    integration = ReturnActionsIntegration()
    order_id, email = integration._extract_order_info(
        "yes please go ahead with that", {"email": "customer@example.com"}, {},
        intent_result=IntentResult(action_type="change_address", order_id=None, raw_address="123 Main St", confidence=0.8),
        ticket_id="ticket-1",
    )
    with patch("src.services.return_actions_integration.supabase_select",
               return_value=[{"id": "ticket-1", "detected_order_id": "1234"}]):
        order_id, email = integration._extract_order_info(
            "yes please go ahead with that", {"email": "customer@example.com"}, {},
            intent_result=IntentResult(action_type="change_address", order_id=None, raw_address="123 Main St", confidence=0.8),
            ticket_id="ticket-1",
        )
    assert order_id == "1234"


# ── Isolation: the ticket-state lookup is scoped to the exact ticket ───────

def test_ticket_state_fallback_only_reads_the_given_ticket_id():
    integration = ReturnActionsIntegration()
    captured = {}

    def fake_select(table, params=None):
        captured["params"] = params
        return []

    with patch("src.services.return_actions_integration.supabase_select", side_effect=fake_select):
        integration._extract_order_info(
            "cancel it", {"email": "c@example.com"}, {},
            intent_result=IntentResult(action_type="cancel", order_id=None, raw_address=None, confidence=0.8),
            ticket_id="ticket-brand-a",
        )
    assert captured["params"] == {"id": "eq.ticket-brand-a"}


# ── 2. Resolved-ticket reopening: threading fix on the second send path ───

def test_manual_send_reply_passes_ticket_thread_id_too():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.middleware.tenant_auth import get_current_tenant, TenantContext
    from src.api.routes.tickets import router as tickets_router

    app = FastAPI()
    app.include_router(tickets_router, prefix="/api")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant_id="tenant-1", email="agent@example.com")
    client = TestClient(app)

    ticket = {
        "id": "ticket-1", "brand_id": "brand-1", "store_id": "brand-1",
        "status": "resolved", "customer_email": "customer@example.com",
        "subject": "hi", "messages": [], "gmail_thread_id": "thread-xyz",
    }
    mock_send = AsyncMock(return_value={"success": True})

    with patch("src.api.routes.tickets._assert_ticket_access", new=AsyncMock(return_value=ticket)), \
         patch("src.api.routes.tickets.supabase_select", return_value=[{"id": "brand-1", "gmail_connected": True}]), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=mock_send), \
         patch("src.api.routes.tickets.supabase_update"):
        resp = client.post("/api/tickets/ticket-1/send-reply", json={"body": "Update on your request."},
                            headers={"Authorization": "Bearer test"})

    assert resp.status_code == 200
    _, kwargs = mock_send.call_args
    assert kwargs.get("thread_id") == "thread-xyz"


def test_reply_on_resolved_ticket_reopens_same_ticket_no_duplicate():
    """End-to-end: a reply in the same Gmail thread as a resolved ticket
    must reopen that exact ticket (no new ticket created, no duplicate)."""
    proc = UnifiedMessageProcessor()
    existing_ticket = {
        "id": "ticket-1", "gmail_thread_id": "thread-xyz", "status": "resolved",
        "messages": [{"from": "c@example.com", "body": "thanks!", "direction": "inbound"}],
        "detected_order_id": "1234",
    }
    captured_updates = []
    create_ticket_called = {"value": False}

    def fake_select(table, params=None):
        if table == "tickets":
            if params and "gmail_thread_id" in params:
                return [existing_ticket]
            if params and "gmail_message_id" in params:
                return []
            return [existing_ticket]
        return [{"id": "tenant-1"}]

    def fake_update(table, match, fields):
        if table == "tickets" and match.get("id") == "eq.ticket-1":
            captured_updates.append(fields)

    async def fake_create_ticket(*args, **kwargs):
        create_ticket_called["value"] = True
        return {"id": "should-not-be-created"}

    ai_result = {
        "reply_body": "Sure, happy to help further.", "ai_reply_generated": True,
        "model_used": "m", "ai_usage": {}, "intent": "order_status_inquiry",
        "sentiment": "neutral", "risk_level": "low", "confidence_score": 90, "escalate": False,
    }

    with patch("src.workers.message_processor.supabase_select", side_effect=fake_select), \
         patch("src.workers.message_processor.supabase_update", side_effect=fake_update), \
         patch("src.workers.message_processor.supabase_service.create_ticket", new=AsyncMock(side_effect=fake_create_ticket)), \
         patch("src.workers.message_processor.supabase_service.get_system_settings", new=AsyncMock(return_value={"ai_mode": "active", "confidence_threshold": 0.65})), \
         patch("src.workers.message_processor.supabase_service.get_or_create_customer", new=AsyncMock(return_value={"id": "customer-1", "email": "c@example.com"})), \
         patch("src.workers.message_processor.customer_success_agent.generate_channel_appropriate_response", new=AsyncMock(return_value=ai_result)), \
         patch("src.services.plan_service.record_email_processed"), \
         patch("src.services.plan_service.can_process_ticket", return_value={"allowed": True}), \
         patch("src.services.plan_service.record_ticket_created"), \
         patch("src.services.plan_service.check_limit", return_value={"allowed": True}), \
         patch("src.services.plan_service.check_ai_entitlement", return_value={"allowed": True, "reason": None, "plan": "trial", "trial_expired": False}), \
         patch("src.services.plan_service.record_ai_reply_event"):
        run(proc.process_message("email_incoming", {
            "channel": "email", "customer_email": "c@example.com", "subject": "Re: hi",
            "content": "okay, can you tell me why?", "gmail_thread_id": "thread-xyz",
            "gmail_message_id": "msg-2", "store_id": "brand-1",
        }))

    assert create_ticket_called["value"] is False  # no duplicate ticket
    reopen_update = next((u for u in captured_updates if u.get("status") == "open"), None)
    assert reopen_update is not None
