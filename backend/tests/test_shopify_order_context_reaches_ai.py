"""
"Shopify Order Context Not Reaching Luna" bug reproduction (order #1013,
"hi where is my order? #1013 i have a memory loss i dont remember if i
canceled it or not?").

Traced root cause: get_order_status()'s customer_email ownership check
(tools.py) - a deliberate, tested security control - was returning the
IDENTICAL generic error for two different situations: "this order genuinely
doesn't exist" and "this order exists but the requester's identity doesn't
match it". customer_success_agent.py then told the model, in both cases,
"ORDER LOOKUP FAILED... tell the customer you're unable to pull up their
order" - which is literally false in the second case (the lookup succeeded;
the order was found) and is exactly the wrong reply this bug reports.

Fix does NOT weaken the ownership check (test_get_order_status_customer_
email_mismatch_blocks_access in test_order_inventory_tools.py still passes
unmodified - a mismatched/unverified identity still never gets the order's
real data). It adds a distinct `ownership_mismatch` signal so the prompt
tells the model to ask the customer to confirm their order's email instead
of falsely claiming the lookup failed. For the actual required case - a
verified/matching identity - the full real order context (including
cancellation) now correctly reaches and is used by the model, which these
tests prove end-to-end.
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.agent.customer_success_agent import customer_success_agent  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_FAKE_USAGE = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}


def _fake_ai_response(reply_body: str):
    content = json.dumps({"intent": "order_status_inquiry", "reply_body": reply_body, "risk_level": "low"})
    msg = MagicMock(content=content)
    choice = MagicMock(message=msg)
    return MagicMock(choices=[choice])


def _run_query(query: str, order_status_result: dict, customer_email: str):
    captured = {}

    async def capturing_completion(*args, messages=None, **kwargs):
        captured["messages"] = messages
        return (_fake_ai_response("ok"), "test_provider", "test_model", _FAKE_USAGE)

    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=capturing_completion), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=order_status_result)), \
         patch("src.agent.customer_success_agent.v3_tools.get_orders_by_email", new=AsyncMock(return_value={"success": False})), \
         patch("src.agent.customer_success_agent.brand_knowledge_service.get_brand_context", new=AsyncMock(return_value="")), \
         patch("src.lib.supabase_client.supabase_select", return_value=[]):
        run(customer_success_agent.process_customer_query(
            query=query,
            customer_info={"name": "Syeda", "email": customer_email},
            tenant_id="tenant-1",
            store_id="brand-1",
            ticket_id="ticket-1",
        ))

    return "\n".join(m.get("content", "") for m in captured.get("messages", []))


def test_connected_shopify_valid_order_cancelled_order_context_reaches_and_is_used_by_ai():
    """The required regression case: connected Shopify + valid order number
    + cancelled order (verified/matching identity) -> the AI's own prompt
    actually contains the real, verified order data."""
    order_result = {
        "success": True, "order_number": "1013", "order_id": "1013",
        "status": "unfulfilled", "financial_status": "paid",
        "cancelled_at": "2026-08-22T06:23:54Z",
        "tracking_number": None, "tracking_url": None, "tracking_company": None,
        "shipment_status": None, "shipped_at": None,
        "fulfillments": [], "fulfillment_count": 0,
        "total_amount": "120.00", "items": [], "created_at": "2026-08-21T12:26:24Z",
    }
    prompt_text = _run_query(
        "hi where is my order? #1013 i have a memory loss i dont remember if i canceled it or not?",
        order_result,
        customer_email="customer10@example.com",
    )
    assert "REAL ORDER DATA FROM SHOPIFY" in prompt_text
    assert "CANCELLED: Yes" in prompt_text
    assert "ORDER LOOKUP FAILED" not in prompt_text


def test_ownership_mismatch_never_leaks_order_data_but_does_not_falsely_claim_lookup_failed():
    """Same order number, but the ownership check correctly declined
    (mismatched identity) - the model must be told the truth (confirm the
    order's email) without ever seeing the real order's details, and without
    being told the lookup itself failed."""
    mismatch_result = {"error": "Order #1013 not found.", "order_number": "1013", "ownership_mismatch": True}
    prompt_text = _run_query(
        "hi where is my order? #1013 i have a memory loss i dont remember if i canceled it or not?",
        mismatch_result,
        customer_email="syedahafsa1983@gmail.com",
    )
    assert "ORDER IDENTITY UNVERIFIED" in prompt_text
    assert "confirm the email address" in prompt_text
    # "CANCELLED: Yes" is the literal marker _build_order_context() would emit
    # for real order data (see test_cancelled_order_surfaces_cancellation_in_
    # context) - its absence proves no real order data leaked through. The
    # bare word "CANCELLED" also appears in generic, static system-prompt
    # instructions unrelated to any specific order, so it isn't checked here.
    assert "CANCELLED: Yes" not in prompt_text
    assert "REAL ORDER DATA FROM SHOPIFY" not in prompt_text
    assert "ORDER LOOKUP FAILED" not in prompt_text
