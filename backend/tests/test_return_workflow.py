"""
Full return automation — regression suite (PART 1 / PART 11 items 1-13).

Covers the "return" intent end-to-end through return_actions_integration.py's
handle_return_intent(): order/customer verification, real policy-based
eligibility (reusing actions_manager.check_return_eligibility unchanged - see
test_refund_policy.py for that function's own thorough coverage), truthful
staged-vs-eligible-vs-completed wording, the duplicate-request guard, and the
partial-order item-awareness improvement.

"return" resolves to the exact same "refund" action_type Shopify mutation a
plain refund request does (see return_actions_integration.py's module
comment for why - REST Admin API has no separate Returns-API call) - these
tests assert on that real, shared execution path, not a parallel one.
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
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _intent(action_type="return", order_id="1001", confidence=0.9):
    return IntentResult(action_type=action_type, order_id=order_id, raw_address=None, confidence=confidence)


def _eligible(**overrides):
    order = {
        "eligible": True, "reason": "within return window",
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"id": 1, "title": "Essential Hoodie", "variant_title": "M", "sku": "EH-M", "price": "45.00"}],
        "order_total": "45.00",
    }
    order.update(overrides)
    return order


def _ineligible(reason="Returns must be initiated within 30 days of delivery. This order is 45 days old.", **overrides):
    order = {
        "eligible": False, "reason": reason,
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"id": 1, "title": "Essential Hoodie", "variant_title": "M", "sku": "EH-M", "price": "45.00"}],
    }
    order.update(overrides)
    return order


def _run(query, intent_result=None, eligibility=None, existing_action=None, create_result=None):
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility or _eligible())), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing_action)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value=create_result or {"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query,
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=intent_result or _intent(),
        ))
    return result, mock_create


# ── 1. Eligible return request ─────────────────────────────────────────────

def test_eligible_return_is_staged_for_approval():
    result, mock_create = _run("I ordered this last week but it doesn't fit, can I return it? Order #1001")

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "Refund"  # a return IS a refund mutation here
    assert result["staged"]["success"] is True
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]
    # Truthful wording: staged, not completed.
    assert "has been submitted for review" in result["action_context"].lower()
    assert "has been returned" not in result["action_context"].lower()
    assert "has been refunded" not in result["action_context"].lower()


def test_return_request_natural_phrasing_variants_all_stage():
    """"I want to send this back", "I need to send this back" - realistic
    phrasing, not just the literal word 'return'."""
    for phrase in ["I want to send this back", "I need to send this back please"]:
        result, mock_create = _run(phrase, intent_result=_intent(action_type="return"))
        mock_create.assert_awaited_once()
        assert result["staged"]["success"] is True


# ── 2/3. Ineligible return / outside policy window ──────────────────────────

def test_ineligible_return_outside_window_is_not_staged():
    result, mock_create = _run(
        "How do I return order #1234?",
        eligibility=_ineligible(reason="Returns must be initiated within 30 days of delivery. This order is 45 days old."),
    )

    mock_create.assert_not_called()
    assert "not eligible" in result["action_context"].lower() or "RETURN NOT ELIGIBLE" in result["action_context"]
    assert "30 days" in result["action_context"]
    assert result.get("staged") is None


# ── 4. Non-returnable item (final sale) ──────────────────────────────────────

def test_final_sale_item_is_not_eligible_and_not_staged():
    result, mock_create = _run(
        "I want to return item X",
        eligibility=_ineligible(reason="This order contains items marked as Final Sale and cannot be returned."),
    )

    mock_create.assert_not_called()
    assert "Final Sale" in result["action_context"]
    assert result["action_context"].count("RETURN NOT ELIGIBLE") == 1 or "not eligible" in result["action_context"].lower()


# ── 5. Unknown order ──────────────────────────────────────────────────────

def test_unknown_order_is_staged_for_manual_review_not_silently_dropped():
    result, mock_create = _run(
        "I want a refund for this",
        eligibility={
            "eligible": False, "eligibility_verified": False,
            "reason": "Order #9999 was not found in our system. Our team will verify and process your request manually.",
            "order": None, "items": [], "requires_manual_review": True, "staging_required": True,
        },
    )

    mock_create.assert_awaited_once()
    assert "MANUAL REVIEW" in result["action_context"]
    assert result["staged"]["success"] is True


# ── 6. Wrong customer/order mismatch ─────────────────────────────────────────

def test_sender_email_mismatch_is_staged_for_manual_review_not_auto_eligible():
    """Someone claiming an order number that isn't theirs must never be
    silently treated as eligible - see test_refund_policy.py for the
    eligibility-check side of this; this test is the staging-decision side."""
    result, mock_create = _run(
        "Can I return my order?",
        eligibility={
            "eligible": False, "eligibility_verified": False,
            "reason": "sender email does not match order email on file",
            "order": {"fulfillment_status": "fulfilled"}, "items": [],
            "requires_manual_review": True, "staging_required": True,
        },
    )

    mock_create.assert_awaited_once()
    assert "MANUAL REVIEW" in result["action_context"]


# ── 7. Partial-order return ──────────────────────────────────────────────────

def test_partial_order_return_names_the_specific_item_for_the_approver():
    """Order has 2 items, customer names only one - the approver must be
    told which one, not left to assume the whole order."""
    eligibility = _eligible(items=[
        {"id": 1, "title": "Essential Hoodie", "variant_title": "M", "price": "45.00"},
        {"id": 2, "title": "Canvas Tote Bag", "variant_title": None, "price": "15.00"},
    ])
    result, mock_create = _run(
        "Actually I only want to return the hoodie, not the other item.",
        eligibility=eligibility,
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert "Essential Hoodie" in kwargs["ai_reasoning"]
    assert "SPECIFICALLY ONLY" in kwargs["ai_reasoning"]
    assert "Canvas Tote Bag" not in result["action_context"]  # doesn't imply the tote is being returned
    assert "PARTIAL" in result["action_context"]
    assert "Essential Hoodie" in result["action_context"]


def test_multi_item_order_return_without_naming_an_item_stages_normally():
    """No specific item named on a multi-item order - stages the order-level
    request as before (this integration has no per-item return selection
    outside exchange), just without the partial-item framing."""
    eligibility = _eligible(items=[
        {"id": 1, "title": "Essential Hoodie", "variant_title": "M", "price": "45.00"},
        {"id": 2, "title": "Canvas Tote Bag", "variant_title": None, "price": "15.00"},
    ])
    result, mock_create = _run("I want to return my order #1001", eligibility=eligibility)

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert "SPECIFICALLY ONLY" not in kwargs["ai_reasoning"]
    assert "PARTIAL" not in result["action_context"]


# ── 8. Already-returned/refunded item ────────────────────────────────────────

def test_already_refunded_order_is_not_staged_again():
    result, mock_create = _run(
        "I want a refund for this",
        eligibility={
            "eligible": False, "reason": "This order has already been refunded in full.",
            "order": {"fulfillment_status": "fulfilled"}, "items": [],
        },
    )

    mock_create.assert_not_called()
    assert "already been refunded" in result["action_context"]


def test_already_cancelled_order_is_not_staged_again():
    result, mock_create = _run(
        "Can I return my order?",
        eligibility={
            "eligible": False, "reason": "This order has already been cancelled.",
            "order": {"fulfillment_status": "unfulfilled"}, "items": [],
        },
    )

    mock_create.assert_not_called()
    assert "already been cancelled" in result["action_context"]


# ── 9/10. Return requiring approval / successful staging ────────────────────

def test_return_stages_for_approval_never_claims_completion():
    result, _ = _run("I want to return my order")
    context_lower = result["action_context"].lower()
    assert "submitted for review" in context_lower
    assert "has been returned" not in context_lower
    assert "refund has been issued" not in context_lower
    assert "completed" not in context_lower


# ── 11. Failed mutation (staging itself fails) ───────────────────────────────

def test_staging_failure_is_reported_not_silently_swallowed():
    result, mock_create = _run(
        "I want to return this",
        create_result={"success": False, "error": "database timeout"},
    )
    mock_create.assert_awaited_once()
    assert "staging failed" in result["action_context"].lower()
    assert "database timeout" in result["action_context"]


# ── 12. Duplicate return request ─────────────────────────────────────────────

def test_duplicate_return_request_does_not_create_a_second_action():
    """"I want to return this" -> (already pending) -> must not stage again."""
    existing = {"action_type": "refund", "status": "pending"}
    result, mock_create = _run("I want to return this", existing_action=existing)

    mock_create.assert_not_called()
    assert "ALREADY PENDING" in result["action_context"]
    assert "do not create a new request" in result["action_context"].lower()


def test_duplicate_return_status_inquiry_reports_true_current_state():
    """"Has the return been processed?" against an already-executed action -
    must answer truthfully from real data, never invent completion."""
    existing = {"action_type": "refund", "status": "executed", "execution_result": {"amount": 45.0, "message": "Refunded"}}
    result, mock_create = _run(
        "Has the return been processed?",
        intent_result=_intent(action_type="refund"),
        existing_action=existing,
    )

    mock_create.assert_not_called()
    assert "ALREADY COMPLETED" in result["action_context"]
    assert "do not invent" in result["action_context"].lower()


def test_duplicate_check_does_not_fire_for_a_different_order():
    """Dedup must be scoped per order - a second, genuinely different
    order's return request must still stage normally."""
    integration = ReturnActionsIntegration()

    async def fake_find_active(tenant_id, order_id, action_type):
        return {"action_type": "refund", "status": "pending"} if order_id == "1001" else None

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=_eligible())), \
         patch.object(integration, "_find_active_action", side_effect=fake_find_active), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True})) as mock_create:
        result = run(integration.handle_return_intent(
            query="I want to return order #2002",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=_intent(action_type="return", order_id="2002"),
        ))

    mock_create.assert_awaited_once()
    assert result["staged"]["success"] is True


# ── 13. Policy-only question does not create a return action ────────────────

def test_policy_question_is_classified_as_none_and_never_reaches_this_handler():
    """"I want to know your return policy" must be classified 'none' by the
    intent detector (see test_intent_detector_usage.py / prompt rules) - if
    it ever were, handle_return_intent must still refuse to act on a 'none'
    intent rather than staging anything."""
    integration = ReturnActionsIntegration()
    none_intent = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.95)
    with patch.object(integration, "_create_action", new=AsyncMock()) as mock_create:
        result = run(integration.handle_return_intent(
            query="I want to know your return policy",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=none_intent,
        ))

    mock_create.assert_not_called()
    assert result["return_checked"] is False
    assert result["action_context"] == ""
