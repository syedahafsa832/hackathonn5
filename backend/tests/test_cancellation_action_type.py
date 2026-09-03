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


# ── 3b. Cancel request on a FULFILLED order needing manual review (e.g.
# sender email doesn't match the order on file) stages a refund, never a
# cancel_order action — the exact reported bug: "can you cancel my order
# #1002" on a fulfilled order produced BOTH a Cancel Order approval AND a
# Refund approval. A fulfilled order can only ever be refunded (Shopify has
# already shipped it), so the "NOT ELIGIBLE and fulfilled" manual-review
# branch (return_actions_integration.py ~line 550) must always stage
# action_type="refund", regardless of the customer's own "cancel" wording. ──

def test_cancel_request_on_fulfilled_order_needing_manual_review_stages_refund_not_cancel():
    eligibility = {
        "eligible": False, "requires_manual_review": True,
        "reason": "sender email does not match order email on file",
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "QA Test Mug", "price": "15.00"}], "order_total": "15.00",
    }
    result, mock_create = _run(
        "can you cancel my order #1002", _intent("cancel", "1002"), eligibility,
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"
    assert kwargs["action_type"] != "cancel_order"
    assert "MANUAL REVIEW" in result["action_context"]


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


# ── 5. The exact reported two-message scenario end-to-end: a fulfilled
# order's cancel request first needs manual review (email mismatch), staging
# a refund action; the customer's very next message confirms their email and
# repeats "cancel my order #1002" — the dedup guard checks for an existing
# action of EITHER action_type ("refund" OR "cancel_order") before ever
# re-running eligibility, so it must recognize the refund action already on
# file and never create a second, contradictory cancel_order action. ───────

def test_followup_message_after_email_confirmation_finds_existing_refund_not_a_new_cancel():
    existing_refund = {"id": "refund-action-1", "action_type": "refund", "status": "pending"}
    # Eligibility is never even reached — _find_active_action short-circuits
    # before check_return_eligibility runs, exactly like the real dedup
    # guard in handle_return_intent (checked "refund" then "cancel_order").
    result, mock_create = _run(
        "the email i ordered from was also mine, and cancel my order #1002",
        _intent("cancel", "1002"), eligibility={}, existing_action=existing_refund,
    )

    mock_create.assert_not_awaited()
    assert "ALREADY PENDING" in result["action_context"]
    assert "staged" not in result


# ── 6. Regression: the reported "Refund / Order #2026 / ... Why approval is
# needed: Customer requests cancel for order #2026" contradiction. The order
# couldn't be found at all (order_id=None, order data empty) — reaching the
# same manual-review branch as test 3b above, still correctly action_type=
# "refund" (unchanged, execution-safety reasons documented at the call site),
# but ai_reasoning must now say so PLAINLY instead of silently naming the
# customer's actual ask ("cancel") with no mention that a refund — a
# different Shopify mutation — is what actually got staged. A reviewer must
# never see a "Refund" badge next to a reason that only ever says "cancel". ──

def test_cancel_request_with_order_not_found_discloses_refund_substitution_in_reasoning():
    eligibility = {
        "eligible": False, "requires_manual_review": True, "staging_required": True,
        "reason": "Order #2026 was not found in our system. Our team will verify and process your request manually.",
        "order": None, "items": [],
    }
    result, mock_create = _run(
        "yes, please go ahead", _intent("cancel", "2026"), eligibility,
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"
    reasoning = kwargs["ai_reasoning"]
    # The customer's real ask and the substitution must BOTH be stated —
    # never just one, which is exactly what produced the self-contradictory
    # card (badge says Refund, reason only ever said "cancel").
    assert "cancel" in reasoning.lower()
    assert "refund" in reasoning.lower()
    assert "Order #2026 was not found" in reasoning


def test_refund_request_with_order_not_found_reasoning_is_not_flagged_as_a_substitution():
    # Sanity check the other direction: a GENUINE refund/return ask reaching
    # this same branch must keep the plain, unqualified wording — there's no
    # substitution to disclose when the stored type already matches the ask.
    eligibility = {
        "eligible": False, "requires_manual_review": True, "staging_required": True,
        "reason": "Order #2026 was not found in our system. Our team will verify and process your request manually.",
        "order": None, "items": [],
    }
    result, mock_create = _run(
        "please refund order #2026", _intent("refund", "2026"), eligibility,
    )

    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"
    reasoning = kwargs["ai_reasoning"]
    assert "Customer requests refund for order #2026" in reasoning
    assert "staged as a" not in reasoning.lower()
