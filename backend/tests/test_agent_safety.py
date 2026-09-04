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

from src.agent.customer_success_agent import (  # noqa: E402
    _enforce_no_unconfirmed_action_success,
    _enforce_human_handoff_request,
    _label_human_request_escalation_reason,
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


# ── Dashboard "Why AI stopped" accuracy: _label_human_request_escalation_reason ──
# Reported bug: "are you ai? i want to talk to human not you" doesn't match
# _HUMAN_HANDOFF_FRAGS above (no article - "talk to a human"), so when the
# model escalates on its own judgment (its RESPONSE schema lets it set
# "escalate": true directly - see _construct_v3_prompt) with a normal 80%
# confidence and "low" risk_level, nothing ever explains why. The dashboard
# then falls back to "The AI was not confident enough..." - false, since the
# AI's reply was generated and sent at full confidence. This function only
# ever LABELS an escalation that already happened; it never sets/clears
# "escalate" or touches "reply_body".

def test_human_request_without_article_labels_the_reported_bug_scenario():
    """The exact reported conversation: model escalated on its own (no
    _HUMAN_HANDOFF_FRAGS match, so escalate/reply_body are already final by
    the time this runs), confidence 80%, risk low. Must be labeled as a
    human request, not left for the dashboard to guess."""
    structured = {
        "intent": "general_inquiry", "action_detected": "none",
        "reply_body": (
            "Hey there, I'm Luna, Hafsa Clothing's AI support! I'm here to help with "
            "orders, sizing, or anything else you need.\n\nwith care,\nLuna"
        ),
        "status": "escalated", "escalate": True, "risk_level": "low",
        "confidence_score": 80,
    }
    result = _label_human_request_escalation_reason(structured, "are you ai? i want to talk to human not you")
    assert result["escalation_reason"] == "Customer explicitly requested a human agent."
    # Never touches the reply or the escalate/status decision itself.
    assert result["reply_body"].startswith("Hey there, I'm Luna")
    assert result["escalate"] is True
    assert result["confidence_score"] == 80


def test_genuine_low_confidence_escalation_is_not_relabeled():
    """No mention of wanting a human at all - a real low-confidence
    escalation must keep showing no escalation_reason here, so the
    dashboard's own genuine "AI wasn't confident enough" text still applies."""
    structured = {
        "intent": "general_inquiry", "action_detected": "none",
        "reply_body": "I'm not fully sure about that - let me get someone to double check.",
        "status": "escalated", "escalate": True, "risk_level": "low",
        "confidence_score": 45,
    }
    result = _label_human_request_escalation_reason(
        structured, "does the blue hoodie run small in the shoulders for a size 14?"
    )
    assert "escalation_reason" not in result


def test_successful_reply_escalated_for_human_request_reply_is_never_touched():
    """AI successfully generated and (per the caller) already sent a reply;
    escalation is because the customer asked for a human - the labeled
    reason must never look like the AI failed to reply, and the reply text
    itself must be completely unchanged."""
    original_reply = "Hey there, happy to help! Let me know what you need."
    structured = {
        "intent": "general_inquiry", "action_detected": "none",
        "reply_body": original_reply,
        "status": "escalated", "escalate": True, "risk_level": "low",
        "confidence_score": 85,
    }
    result = _label_human_request_escalation_reason(structured, "ok but speak to human please")
    assert result["escalation_reason"] == "Customer explicitly requested a human agent."
    assert "not confident" not in result["escalation_reason"].lower()
    assert "failed" not in result["escalation_reason"].lower()
    assert result["reply_body"] == original_reply


def test_existing_escalation_reason_is_preserved_even_if_human_is_mentioned():
    """An earlier backstop already explained why (e.g. the false-success
    guard, or the model's own reason) - must never be overwritten, even
    when the message also happens to mention a human."""
    structured = {
        "intent": "refund_request", "action_detected": "refund",
        "reply_body": "I've sent this to our team to confirm before anything changes.",
        "status": "escalated", "escalate": True, "risk_level": "medium",
        "escalation_reason": "AI reply claimed an unconfirmed action was completed - routed to human review.",
    }
    result = _label_human_request_escalation_reason(structured, "fine, just get me a human then")
    assert result["escalation_reason"] == "AI reply claimed an unconfirmed action was completed - routed to human review."


def test_human_mention_on_a_non_escalated_conversation_is_never_labeled():
    """If the conversation isn't escalating at all, there's nothing to
    label - adding a reason here would misrepresent an auto-resolved
    conversation as escalated-for-a-reason."""
    structured = {
        "intent": "general_inquiry", "action_detected": "none",
        "reply_body": "Sure, here's what I found!",
        "status": "auto_resolved", "escalate": False,
    }
    result = _label_human_request_escalation_reason(structured, "can I speak to human about something else later?")
    assert "escalation_reason" not in result
    assert result["escalate"] is False


# ── PART 2/3 Phase 8.1/8.2 — the Resolution-vs-response-layer boundary
# gap: proven in 8.1, fixed in 8.2 ────────────────────────────────────────
#
# _enforce_no_unconfirmed_action_success now takes an optional
# authoritative_action_type param (_intent_result.action_type - the real,
# backend-classified intent, decided BEFORE the response LLM ever runs).
# When it's "refund" or "cancel", the guard inspects reply_body regardless
# of what the model self-reports in structured["intent"]/["action_detected"].
#
# test_action_detected_alone_without_matching_intent_still_triggers_guard
# above already proves ONE self-reported field being wrong isn't enough to
# defeat the guard (it's an OR check). This test proves the harder case
# Phase 8 flagged: the LLM mislabeling BOTH self-reported fields
# simultaneously used to defeat the guard entirely (proven failing in
# Phase 8.1) - now caught via the authoritative backend value instead.
def test_both_fields_mislabeled_to_general_inquiry_defeats_the_completion_guard():
    """AUTHORITATIVE BACKEND STATE: _intent_result.action_type == "refund" -
    a refund action was staged (NOT completed - see
    return_actions_integration.py, nothing is ever synchronously executed
    here), so action_context/Resolution would say something like "ACTION
    STAGED FOR APPROVAL", never "processed".

    THE LLM'S OWN REPLY mislabels both self-reported fields to
    "general_inquiry"/"none" - as if this were an unrelated question - while
    still writing a false completion claim into reply_body. Passing the
    authoritative_action_type (as the real call site in
    generate_channel_appropriate_response now does) must still catch this,
    regardless of the model's mislabeling."""
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Good news, your refund has been processed!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="refund")

    assert result["escalate"] is True, (
        "GAP STILL PRESENT: _enforce_no_unconfirmed_action_success did not fire even "
        "given the authoritative backend action_type='refund', despite both self-reported "
        "structured['intent']/['action_detected'] being mislabeled to safe values."
    )
    assert "processed" not in result["reply_body"].lower()
    assert result["status"] == "escalated"


def test_mislabeled_cancellation_reply_is_still_caught_via_authoritative_state():
    """Same contradiction, cancellation side: authoritative
    _intent_result.action_type == "cancel" (CancellationSpecialist may
    stage a real cancel_order - see PART 2/3 Phase 7), but the LLM
    mislabels both self-reported fields and falsely claims completion."""
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your order has been successfully cancelled!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="cancel")
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_legitimate_pending_response_not_rewritten_even_with_authoritative_action_type():
    """A truthful "sent for approval" reply must survive unchanged even
    when authoritative_action_type is passed - the guard only overrides
    replies that actually contain false-completion language (_FALSE_SUCCESS_RE),
    never legitimate pending/awaiting-approval wording. Mirrors the existing
    test_refund_reply_that_correctly_says_pending_is_left_alone, now also
    exercising the new authoritative-state path."""
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your refund request has been sent for approval. You'll hear back soon!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="refund")
    assert result["escalate"] is False
    assert result["reply_body"] == "Your refund request has been sent for approval. You'll hear back soon!"
    assert result["status"] == "auto_resolved"


# ── PART 2/3 Phase 9.1 — reship/restore_order were missing from the Phase
# 8.2 authoritative-state fix, despite return_actions_integration.py
# staging real, pending-approval actions table rows for both via direct
# _create_action calls (same semantics as refund/cancel). Proven in the
# Phase 9 audit; fixed by adding "reship"/"restore_order" to
# _AUTHORITATIVE_ACTION_TYPES_REQUIRING_COMPLETION_CHECK and extending
# _FALSE_SUCCESS_RE's vocabulary to "shipped"/"restored" ─────────────────

def test_mislabeled_reship_reply_falsely_claiming_shipped_is_caught_via_authoritative_state():
    """AUTHORITATIVE BACKEND STATE: _intent_result.action_type == "reship" -
    a reship action was staged for manual review, nothing shipped yet. The
    LLM mislabels both self-reported fields to safe values (general_inquiry/
    none, neither of which was ever in _UNCONFIRMED_ACTION_INTENTS/
    _UNCONFIRMED_ACTION_DETECTED to begin with) while falsely claiming the
    replacement already shipped."""
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Good news, your replacement has already shipped!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="reship")
    assert result["escalate"] is True, (
        "GAP: a false 'already shipped' claim for a staged reship action was not caught."
    )
    assert "shipped" not in result["reply_body"].lower()
    assert result["status"] == "escalated"


def test_mislabeled_reship_reply_using_was_shipped_phrasing_is_caught():
    structured = {
        "intent": "shipping_inquiry",
        "action_detected": "none",
        "reply_body": "Your replacement was shipped this morning.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="reship")
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_mislabeled_restore_order_reply_falsely_claiming_restored_is_caught_via_authoritative_state():
    """Same contradiction, restore_order side: authoritative
    _intent_result.action_type == "restore_order", but the LLM mislabels
    both self-reported fields and falsely claims the order was restored."""
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your order has been restored and is ready to go!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="restore_order")
    assert result["escalate"] is True, (
        "GAP: a false 'restored' claim for a staged restore_order action was not caught."
    )
    assert result["status"] == "escalated"


def test_legitimate_pending_reship_response_not_rewritten():
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your replacement request is awaiting approval. You'll hear back soon!",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="reship")
    assert result["escalate"] is False
    assert result["reply_body"] == "Your replacement request is awaiting approval. You'll hear back soon!"
    assert result["status"] == "auto_resolved"


def test_legitimate_pending_reship_submitted_for_approval_response_not_rewritten():
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your replacement request has been submitted for approval.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="reship")
    assert result["escalate"] is False
    assert result["reply_body"] == "Your replacement request has been submitted for approval."
    assert result["status"] == "auto_resolved"


def test_legitimate_pending_restore_order_response_not_rewritten():
    structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your restoration request is pending approval.",
        "status": "auto_resolved",
        "escalate": False,
    }
    result = _enforce_no_unconfirmed_action_success(structured, authoritative_action_type="restore_order")
    assert result["escalate"] is False
    assert result["reply_body"] == "Your restoration request is pending approval."
    assert result["status"] == "auto_resolved"


def test_refund_and_cancel_authoritative_behavior_unchanged_after_reship_restore_addition():
    """Regression guard: adding reship/restore_order to the authoritative
    set and extending _FALSE_SUCCESS_RE's vocabulary must not change
    refund/cancel's existing Phase 8.2 behavior."""
    refund_structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Good news, your refund has been processed!",
        "status": "auto_resolved",
        "escalate": False,
    }
    refund_result = _enforce_no_unconfirmed_action_success(refund_structured, authoritative_action_type="refund")
    assert refund_result["escalate"] is True
    assert "processed" not in refund_result["reply_body"].lower()

    cancel_structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your order has been successfully cancelled!",
        "status": "auto_resolved",
        "escalate": False,
    }
    cancel_result = _enforce_no_unconfirmed_action_success(cancel_structured, authoritative_action_type="cancel")
    assert cancel_result["escalate"] is True

    pending_structured = {
        "intent": "general_inquiry",
        "action_detected": "none",
        "reply_body": "Your refund request has been sent for approval. You'll hear back soon!",
        "status": "auto_resolved",
        "escalate": False,
    }
    pending_result = _enforce_no_unconfirmed_action_success(pending_structured, authoritative_action_type="refund")
    assert pending_result["escalate"] is False
