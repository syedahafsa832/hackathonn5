"""
Full exchange automation — regression suite (PART 2 / PART 3 / PART 11
items 14-28).

Covers the "exchange" intent end-to-end through
return_actions_integration.py's _handle_exchange(): order/customer
verification, the same eligibility/policy check returns use, item
identification on multi-item orders, LIVE Shopify target-variant
resolution (actions_manager.find_exchange_target), price-difference
handling, the duplicate-request guard, and truthful staged-vs-completed
wording. The actual Shopify mutation (ShopifyClient.create_exchange_draft_order,
wired into actions_service.approve_action's EXCHANGE branch) is covered
separately in test_exchange_execution.py — this file is entirely about
whether a real, live-grounded "exchange" action gets staged (or correctly
doesn't).
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


def _intent(order_id="1001", exchange_target="size L", confidence=0.9):
    return IntentResult(action_type="exchange", order_id=order_id, raw_address=None,
                         confidence=confidence, exchange_target=exchange_target)


def _eligible(**overrides):
    order = {
        "eligible": True, "reason": "within return window",
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"id": 1, "title": "Essential Hoodie", "variant_title": "M", "sku": "EH-M", "price": "45.00"}],
        "order_total": "45.00",
    }
    order.update(overrides)
    return order


def _raw_line_item(**overrides):
    item = {
        "id": 1, "product_id": 555, "variant_id": 9001, "title": "Essential Hoodie",
        "variant_title": "M", "sku": "EH-M", "price": "45.00", "quantity": 1,
    }
    item.update(overrides)
    return item


def _found_target(**overrides):
    target = {
        "found": True, "same_product": True,
        "product_id": 555, "product_title": "Essential Hoodie",
        "variant_id": 9002, "variant_title": "L", "price": 45.0,
        "product_url": "https://store.myshopify.com/products/essential-hoodie",
    }
    target.update(overrides)
    return target


def _run(
    query, intent_result=None, eligibility=None, raw_item=None, target=None,
    existing_action=None, create_result=None,
):
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility if eligibility is not None else _eligible())), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing_action)), \
         patch.object(integration, "_get_raw_line_item", new=AsyncMock(return_value=raw_item if raw_item is not None else _raw_line_item())), \
         patch.object(integration.actions, "find_exchange_target", new=AsyncMock(return_value=target if target is not None else _found_target())), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value=create_result or {"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query,
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=intent_result or _intent(),
        ))
    return result, mock_create


# ── 14. Same product, different size ─────────────────────────────────────────

def test_same_product_different_size_stages_real_exchange_action():
    result, mock_create = _run(
        "I bought the hoodie in M but need L.",
        target=_found_target(same_product=True, variant_title="L", price=45.0),
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "exchange"
    assert kwargs["exchange_target"]["variant_title"] == "L"
    assert kwargs["original_item"]["variant_id"] == 9001  # the RAW line item, not the trimmed eligibility one
    assert kwargs["price_difference"] == 0.0
    assert result["staged"]["success"] is True
    assert "EXCHANGE STAGED FOR APPROVAL" in result["action_context"]
    assert "no additional cost" in result["action_context"].lower() or "no price difference" in result["action_context"].lower()


# ── 15. Same product, different color ────────────────────────────────────────

def test_same_product_different_color_stages_real_exchange_action():
    result, mock_create = _run(
        "Can I exchange this for a different color?",
        intent_result=_intent(exchange_target="black"),
        target=_found_target(variant_title="Black", price=45.0),
    )

    mock_create.assert_awaited_once()
    assert result["staged"]["success"] is True


# ── 16. Different product ────────────────────────────────────────────────────

def test_different_product_exchange_uses_real_policy_not_assumption():
    """A different-product swap is only staged when find_exchange_target
    (which title-searches the live catalog, same_product=False) actually
    resolves a real product - never assumed permitted."""
    result, mock_create = _run(
        "Can I exchange this for a different product, the canvas tote?",
        intent_result=_intent(exchange_target="canvas tote"),
        target=_found_target(same_product=False, product_title="Canvas Tote Bag", variant_title="Default Title", price=15.0),
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["exchange_target"]["same_product"] is False
    assert kwargs["price_difference"] == 15.0 - 45.0  # negative -> should NOT auto-stage as ready; see next test


def test_different_product_cheaper_replacement_escalates_price_difference():
    result, mock_create = _run(
        "Can I exchange this for the canvas tote instead?",
        intent_result=_intent(exchange_target="canvas tote"),
        target=_found_target(same_product=False, product_title="Canvas Tote Bag", variant_title="Default Title", price=15.0),
    )
    mock_create.assert_awaited_once()
    assert "MANUAL REVIEW" in result["action_context"] or "manual review" in result["action_context"].lower()
    assert "$30.00" in result["action_context"] or "30.00" in result["action_context"]
    assert "do not say the exchange is done" in result["action_context"].lower() or "do not say the exchange is done" in result["action_context"].lower()


# ── 17/18/19. Availability ───────────────────────────────────────────────────

def test_replacement_available_and_in_stock_stages_exchange():
    result, mock_create = _run("Is L available? Please exchange it.", target=_found_target(variant_title="L", price=45.0))
    mock_create.assert_awaited_once()
    assert result["staged"]["success"] is True


def test_replacement_out_of_stock_never_promises_the_exchange():
    result, mock_create = _run(
        "Can I exchange this for XL?",
        intent_result=_intent(exchange_target="XL"),
        target={"found": False, "reason": "out_of_stock", "product_title": "Essential Hoodie", "variant_title": "XL"},
    )

    mock_create.assert_not_called()
    assert "OUT OF STOCK" in result["action_context"]
    assert "do not create an exchange action" in result["action_context"].lower()
    assert "do not promise" in result["action_context"].lower() or "promise the exchange" in result["action_context"].lower()


def test_replacement_variant_does_not_exist_tells_customer_real_options():
    result, mock_create = _run(
        "Can I exchange this for a XXXL?",
        intent_result=_intent(exchange_target="XXXL"),
        target={"found": False, "reason": "variant_not_found", "product_title": "Essential Hoodie", "available_options": ["S", "M", "L"]},
    )

    mock_create.assert_not_called()
    assert "DOESN'T EXIST" in result["action_context"] or "doesn't exist" in result["action_context"].lower()
    assert "S" in result["action_context"] and "M" in result["action_context"] and "L" in result["action_context"]
    assert "do not create an exchange action" in result["action_context"].lower()


def test_replacement_product_not_found_at_all():
    result, mock_create = _run(
        "Can I exchange this for a leather jacket?",
        intent_result=_intent(exchange_target="leather jacket"),
        target={"found": False, "reason": "target_not_found", "query": "leather jacket"},
    )

    mock_create.assert_not_called()
    assert "PRODUCT NOT FOUND" in result["action_context"]
    assert "do not create an exchange action" in result["action_context"].lower()


def test_ambiguous_target_product_asks_rather_than_guesses():
    result, mock_create = _run(
        "Can I exchange this for the other hoodie?",
        intent_result=_intent(exchange_target="hoodie"),
        target={"found": False, "reason": "ambiguous", "matches": ["Essential Hoodie V2", "Premium Hoodie"]},
    )

    mock_create.assert_not_called()
    assert "DO NOT GUESS" in result["action_context"]
    assert "Essential Hoodie V2" in result["action_context"]
    assert "Premium Hoodie" in result["action_context"]


# ── 20. Price difference (positive) ──────────────────────────────────────────

def test_positive_price_difference_stages_with_balance_shown_never_invented_policy():
    result, mock_create = _run(
        "Can I exchange this for the Premium Hoodie?",
        intent_result=_intent(exchange_target="premium hoodie"),
        target=_found_target(same_product=False, product_title="Premium Hoodie", variant_title="M", price=65.0),
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["price_difference"] == 20.0
    assert "$20.00" in result["action_context"]
    assert "checkout link" in result["action_context"].lower()
    # Never invents how the difference is collected beyond "you'll get a checkout link" (the real, safe mechanism)
    assert "charged automatically" not in result["action_context"].lower()
    assert "card on file" not in result["action_context"].lower()


# ── 21. Ineligible exchange ───────────────────────────────────────────────────

def test_ineligible_exchange_is_not_staged():
    result, mock_create = _run(
        "Can I exchange this?",
        eligibility={
            "eligible": False, "reason": "This order contains items marked as Final Sale and cannot be returned.",
            "order": {"fulfillment_status": "fulfilled"}, "items": [],
        },
    )

    mock_create.assert_not_called()
    assert "EXCHANGE NOT ELIGIBLE" in result["action_context"]
    assert "Final Sale" in result["action_context"]


# ── 22. Unknown order ─────────────────────────────────────────────────────────

def test_unknown_order_exchange_goes_to_manual_review():
    result, mock_create = _run(
        "Can I exchange this for a L?",
        eligibility={
            "eligible": False, "reason": "Order #9999 was not found in our system. Our team will verify and process your request manually.",
            "order": None, "items": [], "requires_manual_review": True, "staging_required": True,
        },
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"  # can't verify -> generic review action, no fabricated target data
    assert "MANUAL REVIEW" in result["action_context"]


# ── 23. Wrong customer/order mismatch ────────────────────────────────────────

def test_sender_mismatch_exchange_goes_to_manual_review_not_auto_eligible():
    result, mock_create = _run(
        "Can I exchange my order?",
        eligibility={
            "eligible": False, "reason": "sender email does not match order email on file",
            "order": {"fulfillment_status": "fulfilled"}, "items": [],
            "requires_manual_review": True, "staging_required": True,
        },
    )

    mock_create.assert_awaited_once()
    assert "MANUAL REVIEW" in result["action_context"]


# ── 24. Partial-order exchange (PART 3: never exchange the whole order) ─────

def test_multi_item_order_exchange_asks_which_item_never_guesses():
    eligibility = _eligible(items=[
        {"id": 1, "title": "Essential Hoodie", "variant_title": "M", "price": "45.00"},
        {"id": 2, "title": "Canvas Tote Bag", "variant_title": None, "price": "15.00"},
    ])
    result, mock_create = _run("Can I exchange this for a L?", eligibility=eligibility)

    mock_create.assert_not_called()
    assert "WHICH ITEM" in result["action_context"]
    assert "Essential Hoodie" in result["action_context"]
    assert "Canvas Tote Bag" in result["action_context"]
    assert "do not create an action" in result["action_context"].lower()


def test_multi_item_order_exchange_with_named_item_only_exchanges_that_one():
    eligibility = _eligible(items=[
        {"id": 1, "title": "Essential Hoodie", "variant_title": "M", "price": "45.00"},
        {"id": 2, "title": "Canvas Tote Bag", "variant_title": None, "price": "15.00"},
    ])
    result, mock_create = _run(
        "I want to exchange just the Essential Hoodie for a L, not the tote bag.",
        eligibility=eligibility,
        raw_item=_raw_line_item(),  # matches the hoodie
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["original_item"]["title"] == "Essential Hoodie"
    assert "Canvas Tote Bag" not in result["action_context"]


# ── 25. Duplicate exchange request ───────────────────────────────────────────

def test_duplicate_exchange_request_does_not_create_a_second_action():
    existing = {"action_type": "exchange", "status": "pending"}
    result, mock_create = _run("Just checking, please exchange it.", existing_action=existing)

    mock_create.assert_not_called()
    assert "EXCHANGE ALREADY PENDING" in result["action_context"]


def test_duplicate_exchange_status_inquiry_reports_true_state_not_invented():
    """"Did you do it?" against an approved-but-not-yet-executed exchange."""
    existing = {"action_type": "exchange", "status": "approved"}
    result, mock_create = _run(
        "Did you do it?",
        intent_result=_intent(exchange_target=None),  # a status check names no new target
        existing_action=existing,
    )

    mock_create.assert_not_called()
    assert "APPROVED" in result["action_context"]
    assert "finishing it up" in result["action_context"].lower() or "being finished" in result["action_context"].lower()


# ── 26. Exchange requiring approval (baseline — already covered by #14, kept
#         explicit here as its own numbered PART 11 case) ──────────────────

def test_exchange_always_requires_approval_never_auto_executes():
    result, mock_create = _run("I bought the hoodie in M but need L.")
    assert "STAGED FOR APPROVAL" in result["action_context"]
    assert "our team" in result["action_context"].lower() or "for approval" in result["action_context"].lower()


# ── 27. Failed exchange mutation (staging itself fails) ─────────────────────

def test_exchange_staging_failure_is_reported_not_silently_swallowed():
    result, mock_create = _run("I bought the hoodie in M but need L.", create_result={"success": False, "error": "database timeout"})
    mock_create.assert_awaited_once()
    assert "staging failed" in result["action_context"].lower()
    assert "database timeout" in result["action_context"]


# ── 28. Policy-only question does not create exchange action ────────────────

def test_exchange_policy_question_is_classified_none_and_never_acted_on():
    integration = ReturnActionsIntegration()
    none_intent = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=0.9)
    with patch.object(integration, "_create_action", new=AsyncMock()) as mock_create:
        result = run(integration.handle_return_intent(
            query="Do you offer exchanges?",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            intent_result=none_intent,
        ))

    mock_create.assert_not_called()
    assert result["return_checked"] is False


# ── PART 3 extras: no target named yet, requires a clarifying question ──────

def test_exchange_with_no_target_named_yet_asks_instead_of_guessing():
    result, mock_create = _run("I want to exchange this.", intent_result=_intent(exchange_target=None))

    mock_create.assert_not_called()
    assert "WHAT REPLACEMENT" in result["action_context"]
    assert "do not create an action" in result["action_context"].lower()


def test_no_shopify_connection_escalates_instead_of_guessing():
    result, mock_create = _run(
        "Can I exchange this for a L?",
        target={"found": False, "reason": "no_shopify_connection"},
    )
    mock_create.assert_not_called()
    assert "CAN'T CHECK LIVE AVAILABILITY" in result["action_context"]
