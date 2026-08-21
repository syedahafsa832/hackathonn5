"""
Exchange workflow — customer wording (superseded/updated).

This file previously covered the old, honest-but-limited exchange behavior:
Shopify's REST Admin API had no swap-line-item operation, so a "wrong size"
request was staged as a plain REFUND and the customer was told to place a
new order themselves. That workaround is now superseded by a real exchange
workflow (return_actions_integration.py's _handle_exchange, backed by
ShopifyClient.create_exchange_draft_order) — "exchange" is its own detected
intent, resolved against LIVE Shopify variant/product data, and staged as
its own "exchange" action type that actually executes a real Shopify
mutation on approval. See test_exchange_workflow.py for the full new
behavior; this file keeps the one test whose premise didn't change (a plain
refund request, unrelated to exchange, must still say a refund is pending -
never a completion claim).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _eligible_order(**overrides):
    order = {
        "eligible": True, "reason": "within return window",
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Essential Hoodie", "variant_title": "M", "sku": "EH-M"}],
        "order_total": "50.00",
    }
    order.update(overrides)
    return order


def _intent(action_type="refund", order_id="1001"):
    return IntentResult(action_type=action_type, order_id=order_id, raw_address=None, confidence=0.9)


def test_plain_refund_request_wording_is_unaffected():
    """Non-exchange refund requests keep their existing wording - never
    claims completion, always says the request is pending review."""
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=_eligible_order())), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})):
        result = run(integration.handle_return_intent(
            query="This item arrived damaged, I want a refund.",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=_intent(),
        ))

    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]
    assert "refund request has been submitted for review" in result["action_context"].lower()
