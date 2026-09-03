"""
Part 1 of the intent/state/action foundation rework.

Production bug: order #1009 (fulfilled), customer Bushra Zohaib. Earlier
conversation: "I want to cancel order #1009" -> a pending cancel_order
action was staged. Later, a NEW message: "I'd like to return order #1009.
Can you check if I'm eligible and let me know what happens next?" (a RETURN
request, not a cancellation and not a refund) got answered:

    "Your cancellation request for order #1009 is already with our team
    for approval, and that process will also cover the return you're
    asking about. You don't need to submit a separate request, as the
    refund you're expecting is included in this single pending approval."

Three separate contaminations in one reply: the current RETURN intent got
described as a CANCELLATION, an unrelated pending cancel_order silently
"resolved" it, and a REFUND was claimed to be included - none of which the
customer asked for or is actually true.

Root causes traced in return_actions_integration.py's shared "RETURN /
REFUND / CANCEL" block (handle_return_intent):

1. The duplicate-request guard's existing_action lookup (originally fixed
   for "refund" intent only in a prior pass) still let intent_type=="return"
   be short-circuited by an existing refund/cancel_order action of ANY
   status - "existing actions... must not automatically define the current
   intent", but that's exactly what happened: an old, unrelated action
   silently answered a brand-new, different request.
2. There is no "return" executable action type anywhere in this system.
   Every path that eventually staged something for a return intent
   (fulfilled+eligible, fulfilled+needs-review) stages action_type="refund"
   or "cancel_order" - RETURN silently became REFUND/CANCEL at the
   EXECUTABLE ACTION layer, not just in wording.

Fix: intent_type=="return" is now handled by its own dedicated branch,
reached only after the real eligibility/policy/identity context has been
gathered (never fabricated), that NEVER calls _create_action and ALWAYS
escalates to a human - matching this codebase's product decision that
returns/exchanges are not automated yet. Any existing refund/cancel_order
action for the order is still looked up and surfaced as INFORMATIONAL
CONTEXT only (never as something that resolves the return).

These tests exercise handle_return_intent() directly with intent_result
already resolved (the same pattern every other test in this codebase
uses - the live LLM classifier itself isn't called in tests; its prompt
wording is covered separately by the deterministic keyword-fallback
classifier tests in Section A below, which exercise a real, non-mocked
code path).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402

from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult, _keyword_fallback  # noqa: E402


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_TENANT = "tenant-intent-1"
_ORDER = "1009"
_CUSTOMER_EMAIL = "bushrazohaib84@gmail.com"
_CUSTOMER_NAME = "Bushra Zohaib"


# ═══════════════════════════════════════════════════════════════════════
# A. INTENT — explicit requests classify distinctly (deterministic
#    keyword-fallback classifier: a real code path, not a mock; the LLM
#    prompt encodes the same distinctions, see intent_detector.py's
#    INTENT_PROMPT "return"/"refund"/"none" bullets)
# ═══════════════════════════════════════════════════════════════════════

def test_1_explicit_cancellation_classifies_as_cancel():
    result = _keyword_fallback("I want to cancel order #1009")
    assert result.action_type == "cancel"


def test_2_explicit_refund_classifies_as_refund():
    result = _keyword_fallback("I want a refund for order #1009")
    assert result.action_type == "refund"


def test_3_explicit_return_classifies_as_return():
    result = _keyword_fallback("I want to return order #1009")
    assert result.action_type == "return"


def test_4_return_window_question_classifies_as_return_not_refund():
    result = _keyword_fallback("Is order #1009 still within the return window?")
    assert result.action_type == "return"
    assert result.action_type != "refund"


# ═══════════════════════════════════════════════════════════════════════
# Shared harness for handle_return_intent()-level tests (B through F)
# ═══════════════════════════════════════════════════════════════════════

def _eligible_fulfilled_order(**overrides):
    data = {
        "eligible": True,
        "order": {"fulfillment_status": "fulfilled", "total_price": "15.00", "currency": "USD"},
        "items": [{"title": "QA Test Mug"}],
        "reason": "Great news! Your order is eligible for return. Would you like to process a refund or an exchange?",
        "shipment_status": "delivered",
    }
    data.update(overrides)
    return data


def _existing_cancel_order(status="pending", **overrides):
    row = {
        "id": "cancel-action-1", "ticket_id": "EARLIER-TICKET", "order_id": _ORDER,
        "action_type": "cancel_order", "status": status,
    }
    row.update(overrides)
    return row


def _existing_refund(status="pending", **overrides):
    row = {
        "id": "refund-action-1", "ticket_id": "EARLIER-TICKET", "order_id": _ORDER,
        "action_type": "refund", "status": status,
    }
    row.update(overrides)
    return row


def _handle(
    action_type, order_id=_ORDER, existing_action_by_type=None, query=None,
    eligibility=None, ticket_id="ticket-now",
):
    """existing_action_by_type: {"refund": row_or_None, "cancel_order": row_or_None}
    mirrors _find_active_action's real per-type, per-order lookup."""
    existing_action_by_type = existing_action_by_type or {}
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type=action_type, order_id=order_id, raw_address=None, confidence=0.9)
    query = query or f"Message about order #{order_id}"

    async def _fake_find_active_action(t_id, o_id, act_type):
        if o_id != order_id:
            return None
        return existing_action_by_type.get(act_type)

    create_mock = AsyncMock(return_value={"success": True, "action_id": "new-action-1"})

    with patch.object(integration, "_find_active_action", new=AsyncMock(side_effect=_fake_find_active_action)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=eligibility or _eligible_fulfilled_order())), \
         patch.object(integration, "_maybe_autopilot_refund", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock(return_value=None)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")):
        result = _run(integration.handle_return_intent(
            query=query,
            customer_info={"name": _CUSTOMER_NAME, "email": _CUSTOMER_EMAIL},
            existing_tool_results={}, tenant_id=_TENANT, brand_id="brand-1",
            ticket_id=ticket_id, intent_result=intent,
        ))
    return result, create_mock


# ═══════════════════════════════════════════════════════════════════════
# B. History does not hijack current intent
# ═══════════════════════════════════════════════════════════════════════

def test_5_previous_cancellation_current_return_stays_return():
    result, create_mock = _handle(
        "return", existing_action_by_type={"cancel_order": _existing_cancel_order(status="pending")},
    )
    create_mock.assert_not_awaited()  # no refund/cancel action manufactured for a return
    text = result["action_context"].lower()
    assert "return" in text
    assert "already covers" not in text
    assert "no need to submit a separate request" not in text


def test_6_previous_refund_current_return_stays_return():
    result, create_mock = _handle(
        "return", existing_action_by_type={"refund": _existing_refund(status="executed")},
    )
    create_mock.assert_not_awaited()
    text = result["action_context"].lower()
    assert "already covers" not in text
    # The return request itself is still escalated, not silently closed out.
    assert "escalate" in text.lower() or "human" in text.lower()


def test_7_previous_return_current_refund_becomes_refund():
    """A prior return request never created any action (see Section D) -
    so a later, genuinely new refund request finds nothing to conflict
    with and proceeds normally."""
    result, create_mock = _handle("refund", existing_action_by_type={})
    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"


def test_8_previous_cancellation_current_refund_becomes_refund():
    """Unresolved (non-executed) cancel_order does not stand in for a
    fresh refund request - a real refund action is staged."""
    result, create_mock = _handle(
        "refund", existing_action_by_type={"cancel_order": _existing_cancel_order(status="pending")},
    )
    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"


# ═══════════════════════════════════════════════════════════════════════
# C. Old actions cannot satisfy a new return request
# ═══════════════════════════════════════════════════════════════════════

def test_9_pending_cancellation_cannot_satisfy_new_return_request():
    result, create_mock = _handle(
        "return", existing_action_by_type={"cancel_order": _existing_cancel_order(status="pending")},
    )
    text = result["action_context"].lower()
    assert "your cancellation request" not in text or "does not" in text or "does not substitute" in text
    assert "no need to submit a separate request" not in text
    assert "covers this refund" not in text
    assert "already covers" not in text


def test_10_pending_refund_cannot_satisfy_new_return_request():
    result, create_mock = _handle(
        "return", existing_action_by_type={"refund": _existing_refund(status="pending")},
    )
    text = result["action_context"].lower()
    assert "already covers" not in text
    assert "no need to submit a separate request" not in text
    create_mock.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# D. Return intent never creates a refund or cancellation executable action
# ═══════════════════════════════════════════════════════════════════════

def test_11_return_intent_does_not_create_refund_action():
    result, create_mock = _handle("return", existing_action_by_type={})
    create_mock.assert_not_awaited()
    assert not result.get("staged")


def test_12_return_intent_does_not_create_cancellation_action():
    """Same assertion, explicit for an unfulfilled order - the branch of
    the OLD shared code that used to stage cancel_order for an unfulfilled,
    not-yet-eligible order must never reach a return intent."""
    result, create_mock = _handle(
        "return",
        eligibility={
            "eligible": False,
            "order": {"fulfillment_status": None, "created_at": "2026-09-01T00:00:00-04:00"},
            "reason": "This order hasn't been delivered yet, so it's not eligible for return.",
            "staging_required": True,
            "action_hint": "cancel_order",
        },
    )
    create_mock.assert_not_awaited()
    assert not result.get("staged")


# ═══════════════════════════════════════════════════════════════════════
# E. Customer-facing wording accuracy
# ═══════════════════════════════════════════════════════════════════════

def test_13_return_response_never_falsely_claims_cancellation_requested():
    result, _create = _handle(
        "return", existing_action_by_type={"cancel_order": _existing_cancel_order(status="pending")},
    )
    text = result["action_context"]
    assert "cancellation request for order" not in text.lower() or "return" in text.lower()
    # Must not instruct the model to describe the customer's own ask as a cancellation.
    assert "your cancellation request" not in text.lower().split("note:")[0]


def test_14_return_response_never_falsely_claims_refund_included():
    result, _create = _handle("return", existing_action_by_type={})
    text = result["action_context"].lower()
    assert "refund you're expecting is included" not in text
    assert "the refund you" not in text


# ═══════════════════════════════════════════════════════════════════════
# F. Conflict surfacing vs. unnecessary clarification
#    (whether the model actually asks a clarifying question is its own
#    judgment call per the product spec - "do not hardcode this exact
#    wording" - so what's verified here is the architectural precondition:
#    a genuine prior action IS surfaced as context the model can act on,
#    and nothing is fabricated when there is no prior action at all.)
# ═══════════════════════════════════════════════════════════════════════

def test_15_genuine_conflict_context_is_surfaced_for_the_model_to_use():
    result, _create = _handle(
        "return", existing_action_by_type={"cancel_order": _existing_cancel_order(status="pending")},
    )
    text = result["action_context"].lower()
    # The existing (different-intent) action's real status is present...
    assert "cancellation" in text and "pending" in text
    # ...but explicitly marked as non-substituting, leaving room for the
    # model to ask the customer to confirm which they actually want.
    assert "does not substitute" in text or "does not" in text


def test_16_no_historical_action_means_no_manufactured_conflict_language():
    result, _create = _handle("return", existing_action_by_type={})
    text = result["action_context"].lower()
    assert "also has a separate" not in text
    assert "note:" not in text


# ═══════════════════════════════════════════════════════════════════════
# G. Delivery state: fulfilled != delivered (Shopify shipment_status,
#    no AfterShip - see actions_manager.check_return_eligibility)
# ═══════════════════════════════════════════════════════════════════════

def test_fulfilled_but_in_transit_return_flags_not_yet_delivered():
    result, create_mock = _handle(
        "return",
        eligibility=_eligible_fulfilled_order(shipment_status="in_transit"),
    )
    create_mock.assert_not_awaited()
    text = result["action_context"].lower()
    assert "in_transit" in text or "not yet" in text


def test_fulfilled_with_unknown_shipment_status_is_not_invented():
    """No carrier-reported shipment_status at all (common case) - must not
    fabricate a delivery claim either way."""
    result, create_mock = _handle(
        "return",
        eligibility=_eligible_fulfilled_order(shipment_status=None),
    )
    create_mock.assert_not_awaited()
    text = result["action_context"].lower()
    assert "in_transit" not in text
    assert "not yet confirmed delivered" not in text


# ═══════════════════════════════════════════════════════════════════════
# H. Safety: a return can never accidentally execute refund/cancellation
# ═══════════════════════════════════════════════════════════════════════

def test_return_on_a_genuinely_eligible_order_still_escalates_never_auto_resolves():
    """Even the happy-path "fully eligible" case never auto-approves a
    return - returns are not automated yet, full stop."""
    result, create_mock = _handle("return", eligibility=_eligible_fulfilled_order())
    create_mock.assert_not_awaited()
    assert not result.get("staged")
    text = result["action_context"].lower()
    assert "escalate" in text or "human" in text


def test_exact_reported_message_1009_return_request():
    """The exact reported #1009 scenario: a pending cancellation exists
    from an earlier, unrelated message; the customer now sends a genuine
    return request. No action staged, no merged cancellation/refund claim."""
    result, create_mock = _handle(
        "return",
        existing_action_by_type={"cancel_order": _existing_cancel_order(status="pending")},
        query="Hi Luna, I'd like to return order #1009. Can you check if I'm eligible and let me know what happens next?",
    )
    create_mock.assert_not_awaited()
    text = result["action_context"].lower()
    assert "already covers" not in text
    assert "no need to submit a separate request" not in text
    assert "the refund you're expecting is included" not in text
