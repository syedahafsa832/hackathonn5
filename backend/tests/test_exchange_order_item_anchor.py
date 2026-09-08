"""
Regression test for ticket #1461e40e.

Customer message: "Hi Luna, I ordered the wrong size on order #1009. Can I
exchange it for another size, and do you have that variant available?"

Processing log showed the order lookup succeeding twice ("Shopify order
found"), yet the final reply was "Hey there, Which product are you asking
about?" — even though order #1009 has a real product/variant on it.

Root cause (confirmed by tracing the actual data flow, not assumed):
get_order_status() (src/services/tools.py) DOES return the order's real
line items (title/variant_title/sku/price/quantity) whenever the order
lookup succeeds — "Shopify order found" is not a bare confirmation, the
product data really is there. That order lookup runs early in
process_customer_query() (src/agent/customer_success_agent.py) whenever the
message contains the word "order", and populates
tool_results["order_status"] with those items.

The bug is a SEPARATE code path never consulting that data: "another size"
matches customer_success_agent.py's _is_variant_followup_query gate (built
for follow-ups like "do you have it in black?"), which resolves its anchor
product via _resolve_recent_product_anchor() ONLY — a search through this
conversation's own chat-history text. On a customer's first message there
is no history, so the anchor always came back None, and the code hard-coded
a "which product?" clarification into tool_results["inventory"]. That
result is then unconditionally applied to the final reply by
_enforce_no_ambiguous_product_claim(), completely discarding whatever the
model composed from the order/exchange context that was actually available.

Fix: _resolve_order_item_anchor() (customer_success_agent.py) is tried as a
second fallback when chat history has nothing — it reads the ALREADY-FETCHED
tool_results["order_status"] line items and resolves the anchor product
whenever the order makes it unambiguous (exactly one item, or the message
names exactly one of several). A genuinely ambiguous multi-item order where
the message doesn't identify the item is untouched — it still asks.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.agent.customer_success_agent import (  # noqa: E402
    customer_success_agent,
    _resolve_order_item_anchor,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _order_status(items, order_number="1009"):
    return {
        "success": True, "source": "shopify", "order_number": order_number,
        "status": "fulfilled", "items": items, "total_amount": "45.00",
    }


# ── _resolve_order_item_anchor: pure unit tests, no agent involved ──────────

def test_no_order_status_returns_none():
    assert _resolve_order_item_anchor({}, "another size") is None


def test_failed_order_lookup_returns_none():
    """Order genuinely not found — must not fabricate an anchor."""
    tool_results = {"order_status": {"success": False, "error": "Order #1009 not found."}}
    assert _resolve_order_item_anchor(tool_results, "another size") is None


def test_single_item_order_resolves_unambiguously():
    """The exact ticket #1461e40e shape: one item on the order — no
    disambiguation needed, "another size" clearly refers to it."""
    tool_results = {"order_status": _order_status([
        {"title": "Black Hoodie", "variant_title": "M", "quantity": 1, "price": "45.00", "sku": "BH-M"},
    ])}
    assert _resolve_order_item_anchor(tool_results, "can I exchange it for another size") == "Black Hoodie"


def test_multi_item_order_with_no_named_item_returns_none():
    """Genuinely ambiguous case from the ticket's own example (Black Hoodie
    M + Blue Jeans 32, customer says only "it") — must still ask, never
    guess which one."""
    tool_results = {"order_status": _order_status([
        {"title": "Black Hoodie", "variant_title": "M"},
        {"title": "Blue Jeans", "variant_title": "32"},
    ])}
    assert _resolve_order_item_anchor(tool_results, "I want to exchange it for a larger size") is None


def test_multi_item_order_resolves_the_item_named_in_the_message():
    """Ticket's other example: "I want to exchange the hoodie for a larger
    size" on a two-item order — the hoodie is identified from the message
    text, matching return_actions_integration.py's _match_order_item
    discipline for the exchange specialist's own item selection."""
    tool_results = {"order_status": _order_status([
        {"title": "Black Hoodie", "variant_title": "M"},
        {"title": "Blue Jeans", "variant_title": "32"},
    ])}
    assert _resolve_order_item_anchor(tool_results, "I want to exchange the hoodie for a larger size") == "Black Hoodie"


# ── Full agent routing (same harness pattern as
#    test_recommendation_context_resolution.py) ─────────────────────────────

def _fake_ai_response(reply_body: str):
    import json
    msg = MagicMock()
    msg.content = json.dumps({"intent": "product_inquiry", "reply_body": reply_body, "risk_level": "low"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _run_query(message: str, order_status_return=None, inv_return=None):
    order_status_return = order_status_return if order_status_return is not None else {"error": "Order not found.", "order_number": None}
    inv_return = inv_return if inv_return is not None else {"success": True, "message": "In stock."}
    with patch("src.services.ai_provider_manager.AIProviderManager.has_providers", new_callable=PropertyMock, return_value=True), \
         patch("src.agent.customer_success_agent.ai_provider_manager.create_chat_completion", new=AsyncMock(return_value=(_fake_ai_response("Here's what I found!"), "test_provider", "test_model", {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "latency_ms": 100, "attempts": 1}))), \
         patch("src.services.intent_detector.intent_detector.detect", new=AsyncMock(return_value=IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9, source="llm"))), \
         patch("src.agent.customer_success_agent.v3_tools.get_order_status", new=AsyncMock(return_value=order_status_return)), \
         patch("src.agent.customer_success_agent.v3_tools.get_inventory_status", new=AsyncMock(return_value=inv_return)) as mock_inv:
        result = run(customer_success_agent.process_customer_query(
            query=message,
            customer_info={"name": "Jane", "email": "jane@example.com", "channel": "chat"},
            tenant_id="tenant-1",
            store_id=None,
            ticket_id="ticket-1",
        ))
    return result, mock_inv


def test_single_item_order_no_longer_asks_which_product():
    """End-to-end reproduction of ticket #1461e40e: with the order lookup
    (triggered by "order #1009" in the message) returning the order's one
    real line item, "another size" must resolve against it — the response
    must NOT fall back to the old hard-coded "which product?" ask."""
    message = (
        "Hi Luna, I ordered the wrong size on order #1009. Can I exchange it "
        "for another size, and do you have that variant available?"
    )
    order_status_return = {
        "success": True, "order_number": "1009", "status": "fulfilled",
        "items": [{"title": "Black Hoodie", "variant_title": "M", "quantity": 1, "price": "45.00", "sku": "BH-M"}],
        "total_amount": "45.00",
    }
    result, mock_inv = _run_query(message, order_status_return=order_status_return)
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "Black Hoodie"
    assert "which product" not in (result.get("reply_body") or "").lower()


def test_multi_item_order_genuinely_ambiguous_still_asks():
    """Regression guard in the OTHER direction: a real multi-product order
    where the message doesn't name which item must still ask — the fix must
    not remove legitimate clarification."""
    message = "I ordered the wrong size on order #1009. Can I exchange it for another size?"
    order_status_return = {
        "success": True, "order_number": "1009", "status": "fulfilled",
        "items": [
            {"title": "Black Hoodie", "variant_title": "M", "quantity": 1, "price": "45.00", "sku": "BH-M"},
            {"title": "Blue Jeans", "variant_title": "32", "quantity": 1, "price": "60.00", "sku": "BJ-32"},
        ],
        "total_amount": "105.00",
    }
    result, mock_inv = _run_query(message, order_status_return=order_status_return)
    mock_inv.assert_not_called()
    assert "which product" in (result.get("reply_body") or "").lower()


def test_multi_item_order_with_named_item_reaches_live_lookup():
    """The order has two items, but the message names the hoodie
    explicitly — must resolve to it directly instead of asking."""
    message = "I ordered the wrong size on order #1009. Can I exchange the hoodie for another size?"
    order_status_return = {
        "success": True, "order_number": "1009", "status": "fulfilled",
        "items": [
            {"title": "Black Hoodie", "variant_title": "M", "quantity": 1, "price": "45.00", "sku": "BH-M"},
            {"title": "Blue Jeans", "variant_title": "32", "quantity": 1, "price": "60.00", "sku": "BJ-32"},
        ],
        "total_amount": "105.00",
    }
    result, mock_inv = _run_query(message, order_status_return=order_status_return)
    mock_inv.assert_called_once()
    assert mock_inv.call_args.args[0] == "Black Hoodie"
