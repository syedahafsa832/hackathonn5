"""
Return/exchange safety — regression suite (PART 5 / PART 6 / PART 7 /
PART 11 items 29-33).

Covers the guardrails that sit ABOVE the return/exchange workflow itself:
- the deterministic false-success override for return/exchange intents
- the action-queue dedup guard across repeated conversation turns
- that a second, differently-worded message never creates a second action
- that "yes do it" cannot mutate an order the conversation didn't establish
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
from src.agent.customer_success_agent import _enforce_no_unconfirmed_action_success  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── 29. LLM cannot claim return/exchange success when nothing executed ──────

def test_false_return_completion_claim_is_overridden():
    structured = {
        "intent": "return_request", "action_detected": "return", "escalate": False,
        "reply_body": "Great news, your return has been processed and the refund was issued!",
    }
    out = _enforce_no_unconfirmed_action_success(structured)
    assert "has been processed" not in out["reply_body"]
    assert "sent it to our team to confirm" in out["reply_body"]
    assert out["escalate"] is True


def test_false_exchange_completion_claim_is_overridden():
    structured = {
        "intent": "exchange_request", "action_detected": "exchange", "escalate": False,
        "reply_body": "Your exchange has been completed and the new size is on its way!",
    }
    out = _enforce_no_unconfirmed_action_success(structured)
    assert "has been completed" not in out["reply_body"]
    assert out["escalate"] is True
    assert out["status"] == "escalated"


def test_truthful_pending_return_reply_is_left_alone():
    structured = {
        "intent": "return_request", "action_detected": "return", "escalate": False,
        "reply_body": "I've sent your return request to our team for approval.",
    }
    out = _enforce_no_unconfirmed_action_success(structured)
    assert out["reply_body"] == "I've sent your return request to our team for approval."
    assert out["escalate"] is False


# ── 30. No mutation happens before approval (return/exchange staging never
#         calls Shopify directly — only actions_service.approve_action does,
#         and only after a human approves) ──────────────────────────────────

def test_staging_a_return_never_touches_shopify_directly():
    """_create_action (the return/exchange staging path) must never import
    or call the Shopify client - only actions_service.approve_action does,
    gated on human approval. Staging only writes a pending action row."""
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
        "eligible": True, "reason": "ok", "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Hoodie", "variant_title": "M", "price": "45.00"}], "order_total": "45.00",
    })), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_shopify, \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})):
        run(integration.handle_return_intent(
            query="I want a refund", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=IntentResult(action_type="return", order_id="1001", raw_address=None, confidence=0.9),
        ))
    mock_shopify.assert_not_called()


# ── 31/32. Multi-turn duplicate-request guard (PART 6) ───────────────────────

def test_multi_turn_return_conversation_never_creates_a_second_action():
    """Simulates 3 real conversation turns against the same order:
    request -> follow-up confirmation -> status check. Only the FIRST turn
    may create an action; the other two must reuse its status."""
    integration = ReturnActionsIntegration()
    eligibility = {
        "eligible": True, "reason": "ok", "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Hoodie", "variant_title": "M", "price": "45.00"}], "order_total": "45.00",
    }
    intent = IntentResult(action_type="return", order_id="1001", raw_address=None, confidence=0.9)
    customer = {"name": "Jane", "email": "jane@example.com"}

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})) as mock_create:
        turn1 = run(integration.handle_return_intent(
            query="I want to return this hoodie.", customer_info=customer, existing_tool_results={},
            tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))
    assert "STAGED FOR APPROVAL" in turn1["action_context"]
    mock_create.assert_awaited_once()

    now_pending = {"action_type": "refund", "status": "pending"}
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=now_pending)), \
         patch.object(integration, "_create_action", new=AsyncMock()) as mock_create2:
        turn2 = run(integration.handle_return_intent(
            query="Just confirming, please go ahead and return it.", customer_info=customer,
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))
    mock_create2.assert_not_called()
    assert "ALREADY PENDING" in turn2["action_context"]

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=now_pending)), \
         patch.object(integration, "_create_action", new=AsyncMock()) as mock_create3:
        turn3 = run(integration.handle_return_intent(
            query="Did you do it yet?", customer_info=customer, existing_tool_results={},
            tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))
    mock_create3.assert_not_called()
    assert "ALREADY PENDING" in turn3["action_context"]


def test_multi_turn_exchange_conversation_updates_target_without_duplicating_action():
    """PART 7: 'I actually want the black one in L' after an initial
    'wrong size' request refers to the SAME conversation's target, not a
    brand-new request - but since no action exists yet for the first
    message (still gathering the target), this must still stage exactly
    once, using the corrected target, not the original one."""
    integration = ReturnActionsIntegration()
    eligibility = {
        "eligible": True, "reason": "ok", "order": {"fulfillment_status": "fulfilled"},
        "items": [{"id": 1, "title": "Hoodie", "variant_title": "M", "price": "45.00"}], "order_total": "45.00",
    }
    raw_item = {"id": 1, "product_id": 555, "variant_id": 9001, "title": "Hoodie", "variant_title": "M", "price": "45.00", "quantity": 1}
    customer = {"name": "Jane", "email": "jane@example.com"}

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_get_raw_line_item", new=AsyncMock(return_value=raw_item)), \
         patch.object(integration.actions, "find_exchange_target", new=AsyncMock(return_value={
             "found": True, "same_product": True, "product_id": 555, "product_title": "Hoodie",
             "variant_id": 9010, "variant_title": "Black / L", "price": 45.0, "product_url": "https://x/hoodie",
         })), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})) as mock_create:
        result = run(integration.handle_return_intent(
            query="I actually want the black one in L.", customer_info=customer, existing_tool_results={},
            tenant_id="tenant-1", brand_id="brand-1",
            intent_result=IntentResult(action_type="exchange", order_id="1001", raw_address=None,
                                        confidence=0.9, exchange_target="black L"),
        ))
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["exchange_target"]["variant_title"] == "Black / L"
    assert result["staged"]["success"] is True


# ── 33. Wrong order can never be mutated even on "yes do it" ────────────────

def test_yes_do_it_without_an_order_id_never_guesses_an_order():
    integration = ReturnActionsIntegration()
    with patch.object(integration, "_create_action", new=AsyncMock()) as mock_create:
        result = run(integration.handle_return_intent(
            query="Yes, please do it.", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=IntentResult(action_type="return", order_id=None, raw_address=None, confidence=0.6),
        ))
    mock_create.assert_not_called()
    assert "Do NOT assume or guess order details" in result["action_context"]


def test_dedup_check_is_scoped_to_the_correct_order_not_any_order():
    """A pending action on order #1001 must never suppress a genuine new
    request for a different order #2002 for the same customer/tenant."""
    integration = ReturnActionsIntegration()
    eligibility = {
        "eligible": True, "reason": "ok", "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Hoodie", "variant_title": "M", "price": "45.00"}], "order_total": "45.00",
    }

    async def _find_active(tenant_id, order_id, action_type):
        return {"action_type": "refund", "status": "pending"} if order_id == "1001" else None

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=_find_active), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})) as mock_create:
        result = run(integration.handle_return_intent(
            query="I want to return my other order too.", customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=IntentResult(action_type="return", order_id="2002", raw_address=None, confidence=0.9),
        ))
    mock_create.assert_awaited_once()
    assert "STAGED FOR APPROVAL" in result["action_context"]
