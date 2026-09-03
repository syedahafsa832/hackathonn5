"""
Production bug: customer has an existing pending `cancel_order` action for
an order, then sends a NEW message asking for a refund on the SAME order.
Luna's reply mixed both ideas into one contradictory message:

    "Your cancellation request for order #1009 is already with our team
    for approval, you'll hear back once it's reviewed, no need to send it
    again. I understand you're looking for a refund, and I've noted your
    request alongside the existing cancellation. Our team will review
    everything carefully..."

The second half ("I've noted your request alongside the existing
cancellation") is fabricated — no second, refund-specific action or
approval was ever created. The customer is left unable to tell whether a
real refund request exists.

Root cause: return_actions_integration.py's refund/cancel duplicate-request
guard checks for either a "refund" or "cancel_order" action on the same
order, and (after the previous fix) correctly told the model the real
record was a "cancellation" — but its wording only ever talked about the
cancellation. It never told the model that this refund ask is the SAME
outcome as the pending cancellation, not a second open question. The model
filled that gap itself.

Business rule this fix encodes (established by the EXISTING implementation,
not invented here):
  - shopify_service.cancel_order() only ever runs for an unfulfilled order
    (Shopify itself refuses ORDER_ALREADY_FULFILLED otherwise), and
    cancelling a paid, unfulfilled order auto-refunds its payment.
  - actions_service.py's own cancel_order confirmation email already tells
    the customer "your refund will appear within 3-5 business days" as
    part of executing that SAME action — never a second one.
  So an EXECUTED cancel_order for an order genuinely satisfies a
  refund/return request for THAT SAME order. This is one-directional: a
  pending refund action never retroactively cancels the order, so a
  "cancel" intent matching an existing "refund" record still gets the
  plain, type-accurate duplicate wording, unchanged.

  FOLLOW-UP FIX (see test_refund_intent_not_converted_to_cancellation.py):
  the above is only true once the cancel_order has actually EXECUTED. A
  still-pending/approved cancel_order is NOT the same guarantee —
  cancel_order() hard-rejects a fulfilled order at execution, so a pending
  cancellation on an order that has since become fulfilled would never
  actually execute or produce a refund. The tests below that use a pending
  cancellation now use an EXECUTED one instead to keep testing the
  genuinely-true case; the pending case is covered separately.

Fix: return_actions_integration.py's refund/cancel duplicate guard now
recognizes this one specific cross-type case (refund/return intent +
existing cancel_order record) and routes it to a new
`_cancellation_covers_refund_context`, which gives the model one
unambiguous, backend-computed instruction: don't create a new refund
request, don't claim a second one was "noted", explain the real
relationship. Every other combination (same-type duplicates, a genuinely
different order, a different tenant, a "cancel" intent matching an
existing "refund" record) is untouched and keeps behaving exactly as the
previous fix left it.
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


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


_TENANT = "tenant-coherence-1"
_ORDER = "5001"
_OTHER_ORDER = "5002"
_CUSTOMER_EMAIL = "bushra@example.com"
_CUSTOMER_NAME = "Bushra"


def _pending_cancellation(order_id=_ORDER, **overrides):
    row = {
        "id": "cancel-action-1",
        "ticket_id": "earlier-ticket",
        "order_id": order_id,
        "action_type": "cancel_order",
        "status": "pending",
    }
    row.update(overrides)
    return row


def _executed_cancellation(order_id=_ORDER, **overrides):
    """A cancel_order that has actually run — Shopify really did cancel and
    auto-refund it, so this is the one case where "covers the refund" is
    genuinely true (see the follow-up fix this file's later tests cover:
    a still-PENDING cancel_order must NOT make this same claim)."""
    return _pending_cancellation(order_id=order_id, status="executed", **overrides)


def _eligible_fulfilled_order():
    return {
        "eligible": True,
        "order": {"fulfillment_status": "fulfilled"},
        "items": [{"title": "Item"}],
    }


def _run_refund_request(
    order_id, existing_action_by_order=None, ticket_id="ticket-now",
    customer_email=_CUSTOMER_EMAIL, customer_name=_CUSTOMER_NAME,
    query=None, tenant_id=_TENANT,
):
    """existing_action_by_order: {(tenant_id, order_id): action_dict} — the
    mock only ever returns a match for the EXACT (tenant_id, order_id) key,
    mirroring _find_active_action's real, hard-scoped supabase filter."""
    existing_action_by_order = existing_action_by_order or {}
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id=order_id, raw_address=None, confidence=0.9)
    query = query or f"Hi Luna, I'd like a refund for order #{order_id}. Can you check if I'm eligible?"

    async def _fake_find_active_action(t_id, o_id, action_type):
        if action_type != "cancel_order":
            return None
        return existing_action_by_order.get((t_id, o_id))

    find_mock = AsyncMock(side_effect=_fake_find_active_action)
    create_mock = AsyncMock(return_value={"success": True, "action_id": f"refund-{order_id}"})

    with patch.object(integration, "_find_active_action", new=find_mock), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=_eligible_fulfilled_order())), \
         patch.object(integration, "_maybe_autopilot_refund", new=AsyncMock(return_value=None)):
        result = _run(integration.handle_return_intent(
            query=query,
            customer_info={"name": customer_name, "email": customer_email},
            existing_tool_results={}, tenant_id=tenant_id, brand_id="brand-1",
            ticket_id=ticket_id, intent_result=intent,
        ))
    return result, find_mock, create_mock


# ── 1 & 3. Existing cancellation genuinely covers the new refund request ──
# (only once it has actually EXECUTED — see the follow-up fix below for why
# a still-pending cancel_order cannot make this same claim)

def test_refund_request_against_existing_cancellation_gives_one_coherent_answer():
    existing = _executed_cancellation()
    result, _find, create_mock = _run_refund_request(_ORDER, {(_TENANT, _ORDER): existing})

    create_mock.assert_not_awaited()
    assert "duplicate_of_existing_action" in result
    assert result["duplicate_of_existing_action"] is existing
    assert "CANCELLATION (COVERING THIS REFUND) ALREADY COMPLETED" in result["action_context"]
    # Never the old, ambiguity-inviting generic wording for this cross-type case.
    assert "REFUND ALREADY PENDING" not in result["action_context"]


def test_no_duplicate_refund_action_or_misleading_noted_request_phrase():
    existing = _executed_cancellation()
    result, _find, create_mock = _run_refund_request(_ORDER, {(_TENANT, _ORDER): existing})

    create_mock.assert_not_awaited()  # no duplicate refund action, no duplicate approval
    text = result["action_context"].lower()
    assert "do not create a new refund request" in text
    # Explains the real cancellation/refund relationship, not silence about it.
    assert "cancellation" in text and "refund" in text


# ── 2. Genuinely separate refund (no related pending action at all) ───────

def test_fresh_order_with_no_pending_action_creates_a_real_new_refund_action():
    """Within this codebase's existing architecture, "genuinely separate"
    only ever means: no cancel_order/refund action exists for THIS order at
    all. Proves that path still creates a real action + real approval and
    reports it honestly — never silently absorbed into any other order's
    cancellation."""
    result, _find, create_mock = _run_refund_request(_OTHER_ORDER, existing_action_by_order={})

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"
    assert create_mock.await_args.kwargs["order_id"] == _OTHER_ORDER
    assert result["staged"]["success"] is True
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]
    assert "duplicate_of_existing_action" not in result


# ── 4. Different order — cannot suppress or satisfy an unrelated order ────

def test_cancellation_on_one_order_never_suppresses_refund_for_a_different_order():
    existing = _pending_cancellation(order_id=_ORDER)
    result, find_mock, create_mock = _run_refund_request(
        _OTHER_ORDER, existing_action_by_order={(_TENANT, _ORDER): existing},
    )

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["order_id"] == _OTHER_ORDER
    assert "duplicate_of_existing_action" not in result
    assert "EXISTING CANCELLATION ALREADY COVERS" not in result["action_context"]
    # The unrelated order's cancellation itself was never read as a match.
    for call in find_mock.await_args_list:
        assert call.args[1] == _OTHER_ORDER  # every lookup was scoped to the order actually being asked about


# ── 5. Different customer/tenant — no cross-tenant leakage ────────────────

def test_cancellation_under_a_different_tenant_never_leaks_across_boundary():
    existing = _pending_cancellation(order_id=_ORDER)
    other_tenant = "tenant-coherence-2"
    result, find_mock, create_mock = _run_refund_request(
        _ORDER, existing_action_by_order={(_TENANT, _ORDER): existing}, tenant_id=other_tenant,
    )

    create_mock.assert_awaited_once()  # a different tenant's order gets its own real check
    assert "duplicate_of_existing_action" not in result
    for call in find_mock.await_args_list:
        assert call.args[0] == other_tenant  # tenant_id was never widened/dropped


# ── 6. Multi-turn continuation — repeat ask stays consistent, no re-staging ─
# (executed cancellation — see the follow-up fix for the still-pending case)

def test_repeat_refund_ask_across_two_turns_stays_consistent_and_never_double_stages():
    existing = _executed_cancellation()
    by_order = {(_TENANT, _ORDER): existing}

    first, _f1, create1 = _run_refund_request(_ORDER, by_order, ticket_id="ticket-turn-1")
    second, _f2, create2 = _run_refund_request(
        _ORDER, by_order, ticket_id="ticket-turn-1",
        query="Hi Luna, just checking again — did you process my refund for #5001?",
    )

    create1.assert_not_awaited()
    create2.assert_not_awaited()
    assert first["action_context"] == second["action_context"]
    assert first["duplicate_of_existing_action"] == second["duplicate_of_existing_action"]


# ── 7. Approval integrity — a genuine new action never inherits the ────────
#      cancellation's metadata

def test_new_refund_action_metadata_is_scoped_to_the_new_request_not_the_cancellation():
    cancellation_from_a_different_ticket_and_customer = _pending_cancellation(
        order_id=_ORDER, ticket_id="OLD-TICKET-DO-NOT-INHERIT",
    )
    # The cancellation exists only for _ORDER; this request is for a
    # different order, so it must be treated as fully independent.
    result, _find, create_mock = _run_refund_request(
        _OTHER_ORDER,
        existing_action_by_order={(_TENANT, _ORDER): cancellation_from_a_different_ticket_and_customer},
        ticket_id="NEW-TICKET-2", customer_email="new-customer@example.com", customer_name="New Customer",
        query=f"Hi, please refund order #{_OTHER_ORDER}, I checked and I'm eligible.",
    )

    create_mock.assert_awaited_once()
    kwargs = create_mock.await_args.kwargs
    assert kwargs["order_id"] == _OTHER_ORDER
    assert kwargs["ticket_id"] == "NEW-TICKET-2"
    assert kwargs["email"] == "new-customer@example.com"
    assert kwargs["customer_name"] == "New Customer"
    assert kwargs["action_type"] == "refund"
    # Nothing from the unrelated cancellation leaked into the new action.
    assert kwargs["ticket_id"] != "OLD-TICKET-DO-NOT-INHERIT"
    assert result["staged"]["success"] is True


# ── Reverse direction stays untouched: a pending REFUND never "covers" a ──
#     later cancel request — plain, type-accurate wording only.

def test_pending_refund_does_not_claim_to_cover_a_later_cancel_request():
    existing_refund = {
        "id": "refund-action-1", "ticket_id": "earlier-ticket",
        "order_id": _ORDER, "action_type": "refund", "status": "pending",
    }
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id=_ORDER, raw_address=None, confidence=0.9)

    async def _fake_find_active_action(t_id, o_id, action_type):
        if action_type == "refund" and (t_id, o_id) == (_TENANT, _ORDER):
            return existing_refund
        return None

    with patch.object(integration, "_find_active_action", new=AsyncMock(side_effect=_fake_find_active_action)), \
         patch.object(integration, "_create_action", new=AsyncMock()) as create_mock:
        result = _run(integration.handle_return_intent(
            query=f"Actually please just cancel order #{_ORDER}",
            customer_info={"name": _CUSTOMER_NAME, "email": _CUSTOMER_EMAIL},
            existing_tool_results={}, tenant_id=_TENANT, brand_id="brand-1",
            ticket_id="ticket-now", intent_result=intent,
        ))

    create_mock.assert_not_awaited()
    text = result["action_context"].lower()
    assert "refund" in text
    assert "covers this refund" not in text
    assert "cancellation" not in text  # existing record is a refund, not a cancellation — noun must match it
