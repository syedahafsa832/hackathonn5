"""
Exchange workflow honesty (P0 product gap).

Shopify's REST Admin API (the only API this integration uses) has no
create-exchange / swap-line-item operation, and none was added here — that
would be a new API surface (GraphQL Returns API / draft orders), which is a
real architectural addition, not a small fix. What already existed and still
exists: a size-exchange request is staged as a REFUND (_ACTION_TYPE_MAP maps
"Exchange" -> "refund" in create_action), which is the only thing that
actually executes on approval.

The gap this fixes: the customer-facing text previously said "your exchange
request has been submitted for review" - implying the exchange itself was in
progress. It never claimed the exchange was *completed*, so it wasn't the
false-success-after-Shopify-confirmation bug the rest of this project has
been hardening against, but it did misrepresent what real, trackable thing
is happening. Fixed to explicitly say a REFUND was staged and the customer
needs to place a new order - with the suggested size mentioned when
suggest_exchange() found one in stock.

Only the customer-facing wording changed. The actual staged action was
always (and remains) a refund - not touched here, already covered by
test_refund_policy.py and test_shopify_action_verification.py.
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


def test_size_exchange_request_is_never_told_the_exchange_itself_is_processing():
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=_eligible_order())), \
         patch.object(integration.actions, "suggest_exchange", new=AsyncMock(return_value={
             "has_exchange": True, "pitch": "", "suggestions": [{"suggested_size": "L", "original_item": "Essential Hoodie"}],
         })), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})):
        result = run(integration.handle_return_intent(
            query="I ordered a medium but need a large, can I exchange it?",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=_intent(),
        ))

    context = result["action_context"].lower()
    # The old, replaced wording claimed the exchange itself was in progress.
    assert "your exchange request has been submitted for review" not in context
    # The new framing is explicit that only a refund is real, and instructs
    # the model never to claim the exchange itself is done.
    assert "refund" in context
    assert "place a new order" in context
    assert "do not say the exchange itself is being processed or completed" in context
    assert "l" in result["action_context"]  # suggested size ("L") surfaced somewhere


def test_size_exchange_request_without_available_size_still_says_refund_not_exchange():
    """No suggest_exchange match (e.g. out of stock) - must still describe
    this as a refund + reorder, not an in-progress exchange."""
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=_eligible_order())), \
         patch.object(integration.actions, "suggest_exchange", new=AsyncMock(return_value={
             "has_exchange": False, "pitch": "out of stock", "suggestions": [],
         })), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})):
        result = run(integration.handle_return_intent(
            query="Can I exchange this for a different size?",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=_intent(),
        ))

    context = result["action_context"].lower()
    assert "refund" in context
    assert "your exchange request has been submitted for review" not in context
    assert "do not say the exchange itself is being processed or completed" in context


def test_plain_refund_request_wording_is_unaffected():
    """Non-size-issue refund requests keep their existing wording - this
    fix is scoped to the exchange/size-issue path only."""
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=_eligible_order())), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})):
        result = run(integration.handle_return_intent(
            query="This item arrived damaged, I want a refund.",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=_intent(),
        ))

    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]
    assert "refund request has been submitted for review" in result["action_context"].lower()
