"""
Rule 1 (safety-non-negotiable): tResolv must never tell a customer a refund,
cancellation, or address change succeeded unless the backend actually confirmed
it. return_actions_integration.py only ever *stages* these for merchant approval
- nothing sensitive is executed synchronously in the reply-generation pipeline.
The system prompt already tells the model never to claim success, but that's a
prompt instruction, not a guarantee. _enforce_no_unconfirmed_action_success in
customer_success_agent.py is the code-level backstop for when the model does it
anyway. These tests cover that backstop directly (pure function, no LLM/mocking
needed).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import _enforce_no_unconfirmed_action_success  # noqa: E402


def test_refund_reply_claiming_processed_is_overridden_and_escalated():
    structured = {
        "intent": "refund_request",
        "action_detected": "refund",
        "reply_body": "Good news, your refund has been processed!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured)
    assert result["escalate"] is True
    assert result["status"] == "escalated"
    assert "processed" not in result["reply_body"].lower()
    assert "escalation_reason" in result


def test_cancellation_reply_claiming_successfully_cancelled_is_overridden():
    structured = {
        "intent": "cancellation_request",
        "action_detected": "cancel_order",
        "reply_body": "Your order was successfully cancelled.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_address_change_reply_claiming_updated_is_overridden():
    structured = {
        "intent": "address_change",
        "action_detected": "change_address",
        "reply_body": "Your shipping address has been updated.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_refund_reply_that_correctly_says_pending_is_left_alone():
    structured = {
        "intent": "refund_request",
        "action_detected": "refund",
        "reply_body": "I've prepared your request and sent it to our team for confirmation.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured)
    assert result["escalate"] is False
    assert result["status"] == "auto_resolved"
    assert "sent it to our team" in result["reply_body"]


def test_unrelated_intent_with_completion_language_is_not_touched():
    """'Completed' talk about shipping/order status isn't a sensitive-action
    claim - the guard must only fire for refund/cancel/address-change replies."""
    structured = {
        "intent": "order_status_inquiry",
        "action_detected": "none",
        "reply_body": "Your order has been completed and shipped out already!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured)
    assert result["escalate"] is False
    assert result["status"] == "auto_resolved"
    assert "completed" in result["reply_body"]


def test_action_detected_alone_without_matching_intent_still_triggers_guard():
    structured = {
        "intent": "general_inquiry",
        "action_detected": "refund",
        "reply_body": "Your refund is confirmed.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured)
    assert result["escalate"] is True
    assert result["status"] == "escalated"
