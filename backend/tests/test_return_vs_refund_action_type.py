"""
Regression coverage for a confirmed-live intent/action mismatch: order
#1006, "Hi Luna, I'd like to return order #1006. Can you check if it's
still within the return window?" produced a dashboard card titled
"Refund Action Card" with "Issue Refund" / "Reject" buttons - the customer
asked about a RETURN, never a refund.

ROOT CAUSE TRACE (see the full writeup in the PR/session report):

1. Intent extraction is already correct. intent_detector.py's LLM prompt
   and keyword fallback (_RETURN_FRAGS, checked before _REFUND_FRAGS, with
   _POLICY_QUESTION_FRAGS checked first so "return window" doesn't get
   mistaken for a policy question) both classify "I'd like to return order
   #1006... still within the return window?" as action_type="return",
   distinct from "refund". Not the bug.

2. return_actions_integration.py's shared "RETURN / REFUND / CANCEL" block
   (handle_return_intent) deliberately stages BOTH the manual-review branch
   and the eligible-happy-path branch with the literal action_type="refund"
   (or "cancel_order" when the order is confirmed unfulfilled) - never
   action_type="return", because this REST-only Shopify integration has no
   separate Returns-API mutation; a refund IS how a return is actually
   fulfilled once a human approves. This collapse is architecturally
   necessary and is NOT being undone here (task scope: no redesign, no
   Shopify API changes).

3. THE ACTUAL BUG: until this fix, nothing preserved the customer's
   original, un-collapsed intent ("return") anywhere queryable. The
   merchant-facing ai_reasoning text already correctly said "Customer
   requests return for order #1006" (matching the reported "Customer
   request: return order #1006"), but the STORED action_type ("refund")
   was the ONLY thing the dashboard (Actions.jsx / ActionCard.jsx) used to
   render the card's type badge, title, and buttons - producing a
   self-contradictory card (reason text says return, badge says Refund).
   The exact same class of bug as the earlier refund/cancellation mismatch
   fixed in return_actions_integration.py's "Fix action_type/ai_reasoning
   mismatch in manual-review staging" - same file, same shared block, same
   missing-structured-intent root cause.

   A second symptom of the same gap: _duplicate_status_context and
   _cancellation_covers_refund_context (the "you already have a pending
   X" replies) derived their wording purely from the EXISTING row's
   action_type/status - never the original intent - so a prior RETURN
   staged as action_type="refund" would be described to the customer as
   "your REFUND request is already pending" on a later message, actively
   telling the customer they asked for something they never asked for.

FIX: _create_action now accepts and stores customer_intent (the raw,
un-collapsed intent_type: "return"/"refund"/"cancel"/"exchange") in
extracted_data, passed at every RETURN/REFUND/CANCEL/EXCHANGE staging
call site in the shared block. action_type/execution are UNCHANGED - this
is purely additive, read-only metadata. _duplicate_status_context and
_cancellation_covers_refund_context now prefer this field when composing
customer-facing wording about an EXISTING record's true type. The
dashboard (separately) uses the same field to show a "Return" badge
instead of "Refund" for a return staged this way - see Actions.jsx's
getActionMeta() and ActionCard.jsx's isReturnStagedAsRefund.

approve_action() (actions_service.py) is verified UNCHANGED here and
still dispatches purely on the stored action_type - customer_intent is
never read at execution time, so this fix cannot let a return silently
become an executable refund action any more than it already could (it
already required, and still requires, explicit human approval either
way).
"""
import os
import sys
import inspect
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult, _keyword_fallback  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _intent(action_type, order_id="1006"):
    return IntentResult(action_type=action_type, order_id=order_id, raw_address=None, confidence=0.9)


def _run(query, intent_result, eligibility, existing_action=None):
    integration = ReturnActionsIntegration()
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligibility)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing_action)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create:
        result = run(integration.handle_return_intent(
            query=query, customer_info={"name": "Jane", "email": "customer@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", intent_result=intent_result,
        ))
    return result, mock_create


_NOT_ELIGIBLE_MANUAL_REVIEW = {
    "eligible": False, "requires_manual_review": True, "staging_required": True,
    "reason": "Return window could not be automatically confirmed for order #1006.",
    "order": {"fulfillment_status": "fulfilled"}, "items": [],
}

_ELIGIBLE_FULFILLED = {
    "eligible": True, "reason": "within return window",
    "order": {"fulfillment_status": "fulfilled"},
    "items": [{"title": "Essential Hoodie", "variant_title": "M", "price": "45.00"}],
    "order_total": "45.00",
}


# ── 0. Intent extraction itself is already correct (not the bug) ──────────

def test_intent_detector_keyword_fallback_classifies_return_window_question_as_return():
    result = _keyword_fallback(
        "Hi Luna, I'd like to return order #1006. Can you check if it's still within the return window?"
    )
    assert result.action_type == "return"
    assert result.order_id == "1006"


def test_intent_detector_keyword_fallback_distinguishes_refund_from_return():
    result = _keyword_fallback("I want a refund for order #1006")
    assert result.action_type == "refund"


# ── 1. Explicit refund request -> refund ────────────────────────────────────

def test_explicit_refund_request_manual_review_stages_refund_with_matching_intent():
    result, mock_create = _run(
        "I want a refund for order #1006", _intent("refund"), _NOT_ELIGIBLE_MANUAL_REVIEW,
    )
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"
    assert kwargs["customer_intent"] == "refund"
    assert "Customer requests refund" in kwargs["ai_reasoning"]


# ── 2. Explicit return request -> the return intent is preserved even though
#     action_type stays "refund" for execution (no separate Shopify Returns
#     mutation exists - see module docstring). This IS the reported bug. ───

def test_explicit_return_request_manual_review_preserves_return_intent():
    result, mock_create = _run(
        "Hi Luna, I'd like to return order #1006. Can you check if it's still within the return window?",
        _intent("return"), _NOT_ELIGIBLE_MANUAL_REVIEW,
    )
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    # action_type is still "refund" - that's the only mutation that can
    # execute this once approved. customer_intent is what tells the
    # dashboard (and any later duplicate-request reply) this was really a
    # return, not a refund ask.
    assert kwargs["action_type"] == "refund"
    assert kwargs["customer_intent"] == "return"
    assert "Customer requests return" in kwargs["ai_reasoning"]
    assert "Customer requests refund" not in kwargs["ai_reasoning"]


# ── 3. Return-window / eligibility question -> return eligibility flow ─────

def test_return_window_question_within_window_stages_as_return_not_refund_ask():
    result, mock_create = _run(
        "Is order #1006 still within the return window?", _intent("return"), _ELIGIBLE_FULFILLED,
    )
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"  # execution constraint, unchanged
    assert kwargs["customer_intent"] == "return"
    assert "Customer requests return" in kwargs["ai_reasoning"]


# ── 4. Refund eligibility question -> refund flow ───────────────────────────

def test_refund_eligibility_question_stages_as_refund():
    result, mock_create = _run(
        "How much of my order will I get refunded?", _intent("refund"), _ELIGIBLE_FULFILLED,
    )
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.call_args
    assert kwargs["action_type"] == "refund"
    assert kwargs["customer_intent"] == "refund"


# ── 5. A previous REFUND action must not be relabeled as a return ──────────

def test_previous_refund_action_is_not_contaminated_by_a_later_return_message():
    existing_refund = {
        "id": "existing-1", "action_type": "refund", "status": "pending",
        "extracted_data": {"customer_intent": "refund"},
    }
    result, mock_create = _run(
        "actually, I want to return order #1006 instead", _intent("return"), {},
        existing_action=existing_refund,
    )
    mock_create.assert_not_awaited()
    assert "REFUND ALREADY PENDING" in result["action_context"]
    assert "RETURN ALREADY PENDING" not in result["action_context"]


# ── 6. A previous RETURN action must not be relabeled as a refund ──────────

def test_previous_return_action_is_not_contaminated_by_a_later_refund_message():
    existing_return = {
        "id": "existing-2", "action_type": "refund", "status": "pending",
        "extracted_data": {"customer_intent": "return"},
    }
    result, mock_create = _run(
        "just give me a refund for order #1006", _intent("refund"), {},
        existing_action=existing_return,
    )
    mock_create.assert_not_awaited()
    assert "RETURN ALREADY PENDING" in result["action_context"]
    assert "REFUND ALREADY PENDING" not in result["action_context"]


# ── 7. A previous CANCELLATION must not be mislabeled as either ────────────

def test_previous_cancellation_does_not_contaminate_a_later_return_message():
    existing_cancel = {"id": "existing-3", "action_type": "cancel_order", "status": "pending"}
    result, mock_create = _run(
        "I'd like to return order #1006", _intent("return"), {}, existing_action=existing_cancel,
    )
    mock_create.assert_not_awaited()
    context = result["action_context"].lower()
    assert "this return request" in context
    assert "the return you're asking about" in context
    assert "this refund request" not in context
    assert "the refund you're asking about" not in context


def test_previous_cancellation_does_not_contaminate_a_later_refund_message():
    existing_cancel = {"id": "existing-4", "action_type": "cancel_order", "status": "pending"}
    result, mock_create = _run(
        "I want a refund for order #1006", _intent("refund"), {}, existing_action=existing_cancel,
    )
    mock_create.assert_not_awaited()
    context = result["action_context"].lower()
    assert "this refund request" in context
    assert "the refund you're asking about" in context
    assert "this return request" not in context


# ── 8 & 9. Customer-facing wording never claims the wrong intent ───────────

def test_eligible_return_customer_facing_text_never_says_refund():
    result, _ = _run(
        "Please return order #1006", _intent("return"), _ELIGIBLE_FULFILLED,
    )
    context = result["action_context"]
    assert "return" in context.lower()
    assert "your refund request" not in context.lower()


def test_eligible_refund_customer_facing_text_never_says_return():
    result, _ = _run(
        "Please refund order #1006", _intent("refund"), _ELIGIBLE_FULFILLED,
    )
    context = result["action_context"]
    assert "refund" in context.lower()
    assert "your return request" not in context.lower()


def test_manual_review_return_generic_customer_reply_never_says_refund_or_return():
    """The manual-review branch's customer-facing text is intentionally
    generic/non-committal (never claims which outcome, per the earlier
    cancel-to-refund fix's precedent) - must stay that way for a return
    too, never drifting into 'your refund is pending'."""
    result, _ = _run(
        "I'd like to return order #1006", _intent("return"), _NOT_ELIGIBLE_MANUAL_REVIEW,
    )
    tell_customer = result["action_context"].split("Tell the customer:")[-1].lower()
    assert "refund" not in tell_customer


# ── 10. Manual-review return: intent stays RETURN, clearly represented ─────

def test_manual_review_return_is_clearly_represented_as_a_return_in_stored_data():
    result, mock_create = _run(
        "Hi Luna, I'd like to return order #1006. Can you check if it's still within the return window?",
        _intent("return"), _NOT_ELIGIBLE_MANUAL_REVIEW,
    )
    _, kwargs = mock_create.call_args
    # The original intent is queryable from the stored row, not just
    # buried in free-text ai_reasoning.
    assert kwargs["customer_intent"] == "return"
    # Nothing about staging itself executes anything - _create_action is
    # the only call made, no Shopify mutation call exists in this path.
    assert mock_create.await_count == 1


# ── 11. Approval execution reads ONLY the stored action_type ───────────────

def test_approve_action_dispatch_never_reads_customer_intent():
    """Execution dispatch must be driven solely by the stored action_type.
    customer_intent (this fix's new field) is a display/wording annotation
    only - approve_action() must never reference it, so a return staged as
    action_type="refund" always executes process_refund() on approval,
    never anything else, regardless of what customer_intent says."""
    from src.services.actions_service import ActionsService
    source = inspect.getsource(ActionsService.approve_action)
    assert "customer_intent" not in source
    assert 'action_type = action["action_type"]' in source
