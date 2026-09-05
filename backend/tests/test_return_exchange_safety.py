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
    """UPDATED for the current RETURN policy (see specialist_resolution.py's
    whitelist / Part 1 of the intent-action foundation rework): a RETURN
    request always escalates to a human and creates NO executable action —
    on every turn, not just repeats after a first one. This still exercises
    the original safety intent of this test (no duplicate/second action
    across a multi-turn conversation), now against the correct baseline of
    zero actions on ANY turn:

    Turn 1: fresh return request, no prior action on file -> still escalates,
            still creates nothing (old assertion here was "STAGED FOR
            APPROVAL" / one action created - that behavior no longer exists).
    Turn 2: a pending refund action now exists for this order (e.g. staged
            separately) -> surfaced only as informational context ("a
            separate refund request... does NOT substitute for this return
            request"), never as something that resolves the return, and
            never a reason to create a second (or any) action.
    Turn 3: a repeated status check against the same pending refund -> same
            guarantee holds; still no action created, still escalates."""
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
    mock_create.assert_not_called()
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in turn1["action_context"]
    assert "STAGED FOR APPROVAL" not in turn1["action_context"]

    now_pending = {"action_type": "refund", "status": "pending"}
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=now_pending)), \
         patch.object(integration, "_create_action", new=AsyncMock()) as mock_create2:
        turn2 = run(integration.handle_return_intent(
            query="Just confirming, please go ahead and return it.", customer_info=customer,
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))
    mock_create2.assert_not_called()
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in turn2["action_context"]
    assert "does NOT substitute for this return request" in turn2["action_context"]
    # Never claims the RETURN itself has an approval pending — none was ever
    # created; only the unrelated refund is genuinely pending.
    assert "RETURN ALREADY PENDING" not in turn2["action_context"]

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=now_pending)), \
         patch.object(integration, "_create_action", new=AsyncMock()) as mock_create3:
        turn3 = run(integration.handle_return_intent(
            query="Did you do it yet?", customer_info=customer, existing_tool_results={},
            tenant_id="tenant-1", brand_id="brand-1", intent_result=intent,
        ))
    mock_create3.assert_not_called()
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in turn3["action_context"]


def test_multi_turn_exchange_conversation_updates_target_without_duplicating_action():
    """UPDATED for PART 2/3 Phase 5 (Exchange Specialist boundary): exchange
    is not automated, so this no longer stages anything at all — but the
    original PART 7 safety intent (a corrected target like 'I actually want
    the black one in L' must use THAT corrected target, not a stale earlier
    one, and must never produce two competing responses) still holds and is
    checked directly against the resolution text instead of mock_create's
    kwargs."""
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
    mock_create.assert_not_awaited()
    assert result.get("staged") is None
    assert "Black / L" in result["action_context"]
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result["action_context"]


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
    """UPDATED for the current RETURN policy: a pending action on order
    #1001 must never leak into how a request for a DIFFERENT order #2002 is
    handled — and, since RETURN now never stages any executable action
    regardless of order (see specialist_resolution.py's whitelist), this
    also confirms a historical action for one order is never mistaken for
    authority over a different order's return request. Order scoping is
    exercised directly by running the same handler against BOTH orders with
    one shared, order-aware `_find_active_action` fake: #2002 has no action
    on file and must get a clean escalation with no mention of #1001's
    refund; #1001 genuinely has one and must see it surfaced as context
    only, never as something that resolves or substitutes for the return.
    Neither order ever gets an executable action created (old assertion
    here — "STAGED FOR APPROVAL" / one action created for #2002 — no longer
    matches current policy)."""
    integration = ReturnActionsIntegration()
    eligibility = {
        "eligible": True, "reason": "ok", "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Hoodie", "variant_title": "M", "price": "45.00"}], "order_total": "45.00",
    }
    lookups = []

    async def _find_active(tenant_id, order_id, action_type):
        lookups.append(order_id)
        return {"action_type": "refund", "status": "pending"} if order_id == "1001" else None

    customer = {"name": "Jane", "email": "jane@example.com"}

    # A genuinely different order (#2002) — no action on file for it at all.
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=_find_active), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})) as mock_create_2002:
        result_2002 = run(integration.handle_return_intent(
            query="I want to return my other order too.", customer_info=customer,
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=IntentResult(action_type="return", order_id="2002", raw_address=None, confidence=0.9),
        ))
    mock_create_2002.assert_not_called()
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result_2002["action_context"]
    # #1001's pending refund must never leak into #2002's reply.
    assert "separate refund request" not in result_2002["action_context"]
    assert "2002" in lookups

    # The order that genuinely HAS a pending action (#1001) — surfaced as
    # context, still never becomes a second action, still never claimed to
    # cover or replace the return.
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration, "_find_active_action", new=_find_active), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})) as mock_create_1001:
        result_1001 = run(integration.handle_return_intent(
            query="I want to return order 1001.", customer_info=customer,
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=IntentResult(action_type="return", order_id="1001", raw_address=None, confidence=0.9),
        ))
    mock_create_1001.assert_not_called()
    assert "ESCALATE TO HUMAN, NO ACTION CREATED" in result_1001["action_context"]
    assert "separate refund request" in result_1001["action_context"]
    assert "does NOT substitute for this return request" in result_1001["action_context"]
    assert "1001" in lookups
