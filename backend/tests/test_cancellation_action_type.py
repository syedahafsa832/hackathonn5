"""
Regression coverage for the duplicate-action bug: "cancel my order #1013"
was producing BOTH a cancel_order action AND a refund action.

Root cause (return_actions_integration.py's shared refund/return/cancel
block): the unfulfilled-order fast path (line ~340) only intercepts the
NOT-eligible case. When check_return_eligibility() returns eligible=True for
an unfulfilled order (a real, reachable state - eligibility doesn't itself
consider fulfillment status disqualifying), execution fell through past that
fast path straight into the generic "ELIGIBLE -> stage the refund" happy
path, which hardcoded action_type="Refund" (also a literal casing bug)
regardless of what the customer actually asked for. A cancellation request
therefore staged a refund-typed action instead of a cancel_order action for
a real, unremarkable case (any unfulfilled order that also happens to pass
a merchant's return-eligibility window check) - and the mirrored
unfulfilled+custom-policy branch had the identical hardcoding bug.

Fixed by deriving action_type from is_unfulfilled (already computed, same
convention the no-custom-policy branch already used) at every creation
point in this shared block, and fixing the "Refund" casing.
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


def _intent(action_type="cancel", order_id="1013"):
    return IntentResult(action_type=action_type, order_id=order_id, raw_address=None, confidence=0.9)


def _run(query, intent_result, eligibility, custom_policy_text="", existing_action=None):
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value=custom_policy_text)), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing_action)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": "customer10@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent_result,
        ))
    return result, mock_create


# ── 1 & 2. Explicit cancellation of an eligible-but-unfulfilled order ──────
# creates exactly one cancel_order action, never a refund action alongside it.

def test_cancellation_of_eligible_unfulfilled_order_creates_one_cancel_order_action():
    eligibility = {
        "eligible": True, "reason": "within return window",
        "order": {"fulfillment_status": "unfulfilled"},
        "items": [{"title": "Essential Hoodie", "price": "45.00"}], "order_total": "45.00",
    }
    result, mock_create = _run(
        "I want to cancel my order #1013 and my email is customer10@example.com",
        _intent("cancel", "1013"), eligibility,
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "cancel_order"
    assert kwargs["action_type"] != "refund" and kwargs["action_type"] != "Refund"
    assert result["staged"]["success"] is True


def test_cancellation_of_unfulfilled_order_with_custom_policy_stages_cancel_order_not_refund():
    """The manual-review (custom-policy) branch had the same bug - fixed
    alongside the eligible-happy-path one above."""
    eligibility = {
        "eligible": False, "reason": "order not yet fulfilled",
        "order": {"fulfillment_status": "unfulfilled"}, "items": [], "order_total": "45.00",
    }
    result, mock_create = _run(
        "please cancel order #1013", _intent("cancel", "1013"), eligibility,
        custom_policy_text="Orders can only be cancelled within 1 hour of purchase.",
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "cancel_order"
    assert "MANUAL REVIEW" in result["action_context"]
    # The raw policy lookup is not thrown away, but it also must not be
    # dumped into the short merchant-facing reason.
    assert "policy_evidence" not in kwargs.get("ai_reasoning", "")
    assert len(kwargs["ai_reasoning"]) < 200


# ── 3. A genuine refund request still creates a refund action ─────────────

def test_genuine_refund_request_on_fulfilled_order_still_creates_refund_action():
    eligibility = {
        "eligible": True, "reason": "within return window",
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Essential Hoodie", "price": "45.00"}], "order_total": "45.00",
    }
    result, mock_create = _run(
        "I'd like a refund for order #1013", _intent("refund", "1013"), eligibility,
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"
    assert result["staged"]["success"] is True


# ── 4. Repeated cancellation request does not create a second action ──────

def test_repeated_cancellation_request_does_not_create_a_second_action():
    existing = {"id": "existing-action-1", "action_type": "cancel_order", "status": "pending"}
    eligibility = {
        "eligible": True, "order": {"fulfillment_status": "unfulfilled"},
        "items": [], "order_total": "45.00",
    }
    result, mock_create = _run(
        "did you cancel my order #1013 yet?", _intent("cancel", "1013"), eligibility,
        existing_action=existing,
    )

    mock_create.assert_not_awaited()
    assert "ALREADY PENDING" in result["action_context"]
    assert "staged" not in result
