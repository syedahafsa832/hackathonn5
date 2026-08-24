"""
Rule 1 (safety-non-negotiable): tResolv must never tell a customer a refund,
cancellation, or address change succeeded unless the backend actually confirmed
it. return_actions_integration.py *stages* these for merchant approval by
default - but Cancellation/Refund Autopilot (once a brand has explicitly
enabled it) can genuinely execute a cancellation/refund synchronously via the
same actions_service.approve_action() a human's Approve click calls. The
system prompt tells the model never to claim success unless RETURN/EXCHANGE
STATUS says so, but that's a prompt instruction, not a guarantee.
_enforce_no_unconfirmed_action_success in customer_success_agent.py is the
code-level backstop for when the model claims success anyway - genuinely_executed
(set by the caller only when the staged action's own record shows
status="executed") is the one legitimate exception, never inferred from the
reply text itself. These tests cover that backstop directly (pure function, no
LLM/mocking needed).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.agent.customer_success_agent import (  # noqa: E402
    _enforce_no_unconfirmed_action_success,
    _enforce_human_handoff_request,
)


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


def test_cancellation_reply_claiming_cancelled_is_left_alone_when_genuinely_executed():
    """The one legitimate exception: Cancellation Autopilot actually executed
    this via actions_service.approve_action() - Shopify confirmed it. The
    same 'successfully cancelled' wording that gets overridden above must NOT
    be touched here, and must NOT be force-escalated - that would recreate
    the exact 'awaiting approval' bug for an order that was already done."""
    structured = {
        "intent": "cancellation_request",
        "action_detected": "cancel_order",
        "reply_body": "Done! Your order has been cancelled successfully.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, genuinely_executed=True)
    assert result["escalate"] is False
    assert result["status"] == "auto_resolved"
    assert "cancelled successfully" in result["reply_body"]


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


# ── Explicit "talk to a human" request always escalates ─────────────────────

def test_explicit_human_request_forces_escalation_even_if_model_did_not_flag_it():
    structured = {
        "intent": "general_inquiry", "action_detected": "none",
        "reply_body": "Sure, I can help with that myself!",
        "status": "auto_resolved", "escalate": False,
    }
    result = _enforce_human_handoff_request(structured, "I want to talk to a human please")
    assert result["escalate"] is True
    assert result["status"] == "escalated"
    assert "team" in result["reply_body"].lower()
    assert "escalation_reason" in result


def test_model_already_escalating_is_left_alone():
    """If the model already correctly escalated, the backstop shouldn't
    stomp on whatever wording/reason it already set."""
    structured = {
        "intent": "general_inquiry", "action_detected": "none",
        "reply_body": "Connecting you with our team now.",
        "status": "escalated", "escalate": True, "escalation_reason": "model's own reason",
    }
    result = _enforce_human_handoff_request(structured, "can I speak to a human")
    assert result["escalation_reason"] == "model's own reason"
    assert result["reply_body"] == "Connecting you with our team now."


def test_human_request_in_stale_chat_history_does_not_retrigger_every_turn():
    """Chat embeds prior turns as 'Customer: ...' lines ahead of the live
    message - only the LATEST message should be checked, not a request
    from several turns ago that's already been handled."""
    structured = {
        "intent": "order_status_inquiry", "action_detected": "none",
        "reply_body": "Your order shipped yesterday!",
        "status": "auto_resolved", "escalate": False,
    }
    stale_history_query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: I want to talk to a human\n"
        "Luna: Connecting you with our team now.\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: where is my order?"
    )
    result = _enforce_human_handoff_request(structured, stale_history_query)
    assert result["escalate"] is False
    assert result["reply_body"] == "Your order shipped yesterday!"


def test_human_request_as_the_live_chat_message_still_escalates():
    structured = {
        "intent": "general_inquiry", "action_detected": "none",
        "reply_body": "Sure, I can help!",
        "status": "auto_resolved", "escalate": False,
    }
    live_query = (
        "[CHAT HISTORY — earlier in this conversation:]\n"
        "Customer: where is my order?\n"
        "Luna: It shipped yesterday!\n"
        "[END CHAT HISTORY]\n\n"
        "Customer: ok but I want to talk to a human"
    )
    result = _enforce_human_handoff_request(structured, live_query)
    assert result["escalate"] is True


def test_unrelated_query_does_not_trigger_human_handoff_guard():
    structured = {
        "intent": "order_status_inquiry", "action_detected": "none",
        "reply_body": "Your order shipped yesterday!",
        "status": "auto_resolved", "escalate": False,
    }
    result = _enforce_human_handoff_request(structured, "where is my order?")
    assert result["escalate"] is False
    assert result["reply_body"] == "Your order shipped yesterday!"
