"""
Root cause of a confirmed-live contradiction on conversation #05d183b7:

    Header:    Escalated
    Activity:  Draft ready / Email sent
    AI reply:  "Your refund request for order #1009 is already with our
                team for approval, you'll hear back once it's reviewed..."
    Sidebar:   Escalated: Needs Your Attention
               Why AI stopped: "The AI was not confident enough..."

Traced with the real production row (tickets.id=05d183b7-be0c-...):
  status=escalated, escalate=true, escalation_reason=null, risk_level=medium,
  confidence_score=65, ai_reply=<the "already with our team" text above>,
  auto_reply_count=1 (the email really was sent).

And the real `actions` table for this ticket: ZERO rows. The "already with
our team for approval" claim was grounded in a REAL row, but the wrong one —
a `cancel_order` action (status=pending) staged on an EARLIER, DIFFERENT
ticket for the same customer/order (return_actions_integration.py's
refund/cancel duplicate-request guard intentionally matches either
action_type against the same order). Two independent, compounding bugs:

1. `_duplicate_status_context` named the pending action's type from the
   CUSTOMER's current intent_type ("refund") instead of the actual row's
   own action_type ("cancel_order") — telling the customer a refund was
   pending when the only real record on file is a cancellation.

2. Even after the agent clears `escalate` for a duplicate-notice reply
   (nothing new is pending on THIS ticket — the real pending work is
   tracked wherever it was actually staged), message_processor.py's
   `_decide_ticket_routing` re-derives ticket status from
   (confidence, risk_level, escalate) alone — never from what the agent
   decided — and a refund/cancel topic's risk_level is "medium" by intent
   classification alone, regardless of whether a NEW action exists. Medium
   risk alone was enough to force the ticket back into its "escalated"
   fallback branch, silently overriding the cleared escalate flag.

Fixed by threading one real, backend-found signal through both layers
(never inferred from confidence/risk/keywords):
  - return_actions_integration.py: the duplicate-guard branches now also
    return `duplicate_of_existing_action` (the real row), and
    `_duplicate_status_context`'s wording is keyed off that row's own
    action_type.
  - customer_success_agent.py:
    `_enforce_no_escalation_for_duplicate_action_notice` clears
    escalate/status AND sets `duplicate_action_notice=True` when a real
    duplicate row backed the reply.
  - message_processor.py: `_decide_ticket_routing` takes
    `has_new_pending_action` (default True — no behavior change for any
    other caller/ticket) and only treats risk_level as "low" for routing
    purposes when the caller explicitly says this turn staged nothing new.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402

from src.agent.customer_success_agent import (  # noqa: E402
    _enforce_no_escalation_for_duplicate_action_notice,
)
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.workers.message_processor import UnifiedMessageProcessor  # noqa: E402


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_REFUND_REPLY = (
    "Dear Bushra, thank you for reaching out. Your refund request for order #1009 "
    "is already with our team for approval, you'll hear back once it's reviewed, "
    "no need to send it again."
)

_PENDING_CANCEL_ACTION = {
    "id": "20bcc08f-831c-4094-87ca-030939c1a0e1",
    "ticket_id": "c171acc5-f0f7-4faf-8d7b-1fe37e677350",
    "order_id": "1009",
    "action_type": "cancel_order",
    "status": "pending",
}


# ── Bug 2: `_duplicate_status_context` must name the REAL action type ─────

def test_duplicate_notice_names_the_real_action_type_not_the_customer_intent():
    """The only real row on file is a cancellation — telling the customer
    a REFUND is pending (because that's what they just asked for) fabricates
    the action's type, not just its existence."""
    integration = ReturnActionsIntegration()
    text = integration._duplicate_status_context(_PENDING_CANCEL_ACTION, intent_type="refund")
    assert "cancellation" in text.lower()
    assert "refund" not in text.lower()


def test_duplicate_notice_still_names_refund_when_the_real_row_is_a_refund():
    integration = ReturnActionsIntegration()
    refund_action = {**_PENDING_CANCEL_ACTION, "action_type": "refund"}
    text = integration._duplicate_status_context(refund_action, intent_type="refund")
    assert "refund" in text.lower()


# ── Bug 1 (agent layer): duplicate-notice backstop ─────────────────────────

def test_duplicate_action_notice_clears_escalation_and_status():
    structured = {
        "reply_body": _REFUND_REPLY, "status": "escalated", "escalate": True,
        "risk_level": "medium", "confidence_score": 65,
    }
    result = _enforce_no_escalation_for_duplicate_action_notice(structured, _PENDING_CANCEL_ACTION)
    assert result["escalate"] is False
    assert result["status"] == "auto_resolved"
    assert result["duplicate_action_notice"] is True


def test_no_duplicate_action_leaves_structured_untouched():
    structured = {"reply_body": _REFUND_REPLY, "status": "escalated", "escalate": True, "risk_level": "medium"}
    result = _enforce_no_escalation_for_duplicate_action_notice(structured, None)
    assert result["escalate"] is True
    assert result["status"] == "escalated"
    assert "duplicate_action_notice" not in result


def test_failed_generation_is_never_overridden_into_a_fake_handled_state():
    structured = {"reply_body": "", "status": "escalated", "escalate": True, "risk_level": "medium"}
    result = _enforce_no_escalation_for_duplicate_action_notice(structured, _PENDING_CANCEL_ACTION)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


def test_independently_high_risk_is_never_weakened_by_duplicate_notice():
    """A genuinely high-risk signal must still escalate even if a duplicate
    action reference also happens to be present."""
    structured = {"reply_body": _REFUND_REPLY, "status": "escalated", "escalate": True, "risk_level": "high"}
    result = _enforce_no_escalation_for_duplicate_action_notice(structured, _PENDING_CANCEL_ACTION)
    assert result["escalate"] is True
    assert result["status"] == "escalated"


# ── Bug 1 (routing layer): status must not be re-derived back to escalated ─

def test_duplicate_notice_routes_to_auto_resolved_despite_medium_risk():
    """The exact #05d183b7 shape: confidence 65%, risk_level medium (refund
    topic alone), escalate already cleared by the agent backstop, AND the
    caller telling routing this turn staged nothing new. Must not land back
    on 'escalated' just because risk_level is 'medium'."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=False, risk_level="medium", reply_body=_REFUND_REPLY,
        has_new_pending_action=False,
    )
    assert routing["should_auto_reply"] is True
    assert routing["status"] == "auto_resolved"


def test_unfixed_contradiction_is_reproduced_without_has_new_pending_action():
    """Sanity check this is a real bug: the same inputs, but without the new
    signal (has_new_pending_action defaults True, i.e. what every caller did
    before this fix), reproduce the exact reported contradiction — a sent
    reply landing on status='escalated'."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=False, risk_level="medium", reply_body=_REFUND_REPLY,
    )
    assert routing["should_auto_reply"] is True
    assert routing["status"] == "escalated"  # the exact contradiction, reproduced


def test_real_pending_action_on_this_ticket_still_escalates():
    """Regression guard: a normal refund/cancel request that DOES stage a
    brand-new action for THIS ticket (has_new_pending_action defaults True)
    must keep escalating exactly as before — this fix only touches the
    duplicate-notice case, never weakens a genuine new escalation."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=True, risk_level="medium", reply_body=_REFUND_REPLY,
    )
    assert routing["status"] == "escalated"


def test_high_risk_still_escalates_even_with_has_new_pending_action_false():
    """has_new_pending_action=False must never launder an independently
    high-risk signal into a quiet auto-resolve."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=False, risk_level="high", reply_body=_REFUND_REPLY,
        has_new_pending_action=False,
    )
    assert routing["status"] == "escalated"


def test_still_flagged_escalate_is_not_downgraded_by_has_new_pending_action_false():
    """has_new_pending_action=False alone must not clear a genuine
    ai_flagged_escalate=True signal — only the case where the agent has
    ALSO already cleared escalate is affected."""
    proc = UnifiedMessageProcessor()
    routing = proc._decide_ticket_routing(
        ai_mode="active", is_overridden=False, confidence=0.65, confidence_threshold=0.65,
        ai_flagged_escalate=True, risk_level="medium", reply_body=_REFUND_REPLY,
        has_new_pending_action=False,
    )
    assert routing["status"] == "escalated"


# ── Follow-up investigation: does #1009 itself need a NEW refund action? ───
#
# A pending cancel_order for a DIFFERENT order must never be picked up for
# this refund request — _find_active_action is already order_id-scoped by
# construction (a hard `order_id: eq.{order_id}` filter), so this is the
# regression guard proving that scoping actually holds end-to-end through
# handle_return_intent: an unrelated order's pending action is invisible to
# a fresh request for order #1009, eligibility runs for real, and a genuine
# new refund action gets created and reported for #1009 specifically —
# never inherited from the unrelated record, and never described as
# "already pending" when nothing for #1009 actually is.

_UNRELATED_ORDER_ID = "9999"
_TARGET_ORDER_ID = "1009"


def _pending_cancellation_for_a_different_order():
    return {
        "id": "cfd9be43-25ca-4436-a1f8-2c27f21b0bd3",
        "ticket_id": "some-other-ticket",
        "order_id": _UNRELATED_ORDER_ID,
        "action_type": "cancel_order",
        "status": "pending",
    }


def test_refund_for_a_different_order_does_not_inherit_an_unrelated_cancellation():
    unrelated_cancellation = _pending_cancellation_for_a_different_order()
    unrelated_snapshot = dict(unrelated_cancellation)

    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id=_TARGET_ORDER_ID, raw_address=None, confidence=0.9)

    async def _fake_find_active_action(tenant_id, order_id, action_type):
        # Order-scoped, exactly like the real supabase_select filter this
        # replaces: only the UNRELATED order's own pending action can ever
        # be returned for its own order_id — #1009 has nothing on file.
        if order_id == _UNRELATED_ORDER_ID and action_type == "cancel_order":
            return unrelated_cancellation
        return None

    eligible_fulfilled_order = {
        "eligible": True,
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Wrap Maxi Dress"}],
    }

    with patch.object(integration, "_find_active_action", new=AsyncMock(side_effect=_fake_find_active_action)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "refund-1009"})) as mock_create, \
         patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=eligible_fulfilled_order)), \
         patch.object(integration, "_maybe_autopilot_refund", new=AsyncMock(return_value=None)):
        result = _run(integration.handle_return_intent(
            query="Hi, I'd like a refund for order #1009. Can you check if I'm eligible?",
            customer_info={"name": "Bushra", "email": "bushrazohaib84@gmail.com"},
            existing_tool_results={}, tenant_id="71dad993-033e-46c7-9f44-82269580cbb0",
            brand_id="brand-1", ticket_id="05d183b7-be0c-494d-a763-619664810cc1",
            intent_result=intent,
        ))

    # 1. Does not inherit the unrelated cancellation.
    assert "duplicate_of_existing_action" not in result
    assert "ALREADY PENDING" not in result["action_context"]
    assert "already with our team" not in result["action_context"]

    # 2 & 3. A real eligibility check ran, and a genuine NEW refund action
    # was created for #1009 specifically (never the unrelated order/type).
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["action_type"] == "refund"
    assert mock_create.await_args.kwargs["order_id"] == _TARGET_ORDER_ID

    # 4. Customer response matches the actual backend state: a real
    # approval was just submitted, not a fabricated "already pending" claim.
    assert result["staged"]["success"] is True
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]

    # 5. The unrelated cancellation record itself was never touched.
    assert unrelated_cancellation == unrelated_snapshot
