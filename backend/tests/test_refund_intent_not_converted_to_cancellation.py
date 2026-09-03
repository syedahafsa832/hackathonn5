"""
Production bug: a customer explicitly asks for a REFUND on a FULFILLED
order (#1009), but Luna replies:

    "Your cancellation request for order #1009 is already with our team
    for approval, and that process will also cover the refund you're
    asking about. You don't need to submit a separate request, as they
    are the same outcome."

The customer never asked to cancel anything, and the order is fulfilled
(Shopify hard-rejects cancelling a fulfilled order), so no cancellation
process can ever "also cover" this refund.

Root cause: return_actions_integration.py's refund/cancel duplicate-request
guard finds an EARLIER, unrelated cancel_order action for the same order
(staged from a completely different conversation) and — regardless of
whether that cancellation has actually executed — tells the model it
"already covers" any new refund ask, via _cancellation_covers_refund_context.
That's only actually true once the cancellation has EXECUTED (Shopify
really did cancel + auto-refund it — see that function's own "executed"
branch). A still-pending or approved-but-unexecuted cancel_order carries no
such guarantee: cancel_order() hard-rejects a fulfilled order at execution
time, so if the order has since become fulfilled, that pending cancellation
will never actually produce a refund - "already covers" was simply false.

Fix (return_actions_integration.py's duplicate-request guard): only short-
circuit with the "cancellation covers this refund" reply when the matched
cancel_order has status="executed". For any other status (pending,
approved, awaiting_manual_step), the guard now falls through to a genuine,
fresh eligibility check — exactly as if no existing action had matched —
so a real `refund` action gets staged (or a truthful non-eligible/manual-
review reply given) for THIS request. The earlier cancel_order is left
completely untouched: never re-staged, never duplicated, never even read
again after this branch decides not to reuse it.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

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


_TENANT = "tenant-refund-intent-1"
_ORDER = "1009"
_CUSTOMER_EMAIL = "bushrazohaib84@gmail.com"
_CUSTOMER_NAME = "Bushra Zohaib"
_REAL_WORLD_MESSAGE = (
    "I’d like a refund for order #1009. Can you check if I’m eligible "
    "and let me know what happens next?"
)


def _pending_cancel_order_from_earlier_conversation(order_id=_ORDER, **overrides):
    """An UNRELATED, still-pending cancel_order staged from a completely
    different ticket/conversation - exactly the "previous conversation
    state leaking in" scenario described in the bug report."""
    row = {
        "id": "earlier-cancel-action",
        "ticket_id": "EARLIER-UNRELATED-TICKET",
        "order_id": order_id,
        "action_type": "cancel_order",
        "status": "pending",
    }
    row.update(overrides)
    return row


def _fulfilled_order_eligibility(eligible=True, **overrides):
    data = {
        "eligible": eligible,
        "order": {"fulfillment_status": "fulfilled", "total_price": "15.00", "currency": "USD"},
        "items": [{"title": "QA Test Mug", "id": 1}],
        "reason": None,
    }
    data.update(overrides)
    return data


def _handle_refund(
    order_id=_ORDER, existing_cancel_order=None, query=None,
    ticket_id="ticket-refund-now", tenant_id=_TENANT,
    customer_email=_CUSTOMER_EMAIL, customer_name=_CUSTOMER_NAME,
    eligibility=None,
):
    """Runs handle_return_intent with intent already resolved to "refund"
    (the customer's actual, correctly-detected intent - this bug is about
    what happens AFTER intent detection, not intent detection itself)."""
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="refund", order_id=order_id, raw_address=None, confidence=0.92)
    query = query or f"I'd like a refund for order #{order_id}. Can you check if I'm eligible?"

    async def _fake_find_active_action(t_id, o_id, action_type):
        if action_type == "cancel_order" and existing_cancel_order and (t_id, o_id) == (tenant_id, order_id):
            return existing_cancel_order
        return None

    create_mock = AsyncMock(return_value={"success": True, "action_id": f"refund-{order_id}"})

    with patch.object(integration, "_find_active_action", new=AsyncMock(side_effect=_fake_find_active_action)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=eligibility or _fulfilled_order_eligibility())), \
         patch.object(integration, "_maybe_autopilot_refund", new=AsyncMock(return_value=None)):
        result = _run(integration.handle_return_intent(
            query=query,
            customer_info={"name": customer_name, "email": customer_email},
            existing_tool_results={}, tenant_id=tenant_id, brand_id="brand-1",
            ticket_id=ticket_id, intent_result=intent,
        ))
    return result, create_mock


# ── 1 & 2. Explicit refund request on a fulfilled order → refund intent,
#      staged action is refund, never cancel ───────────────────────────────

def test_refund_request_on_fulfilled_order_stages_refund_action():
    result, create_mock = _handle_refund(existing_cancel_order=None)

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"
    assert create_mock.await_args.kwargs["action_type"] != "cancel_order"
    assert result["staged"]["success"] is True


def test_refund_request_against_earlier_pending_cancellation_still_stages_refund_not_cancel():
    """The exact bug: an unrelated, still-pending cancel_order for the same
    order must NOT stand in for this refund - a real refund action must be
    staged, and it must never be action_type=cancel_order."""
    earlier_cancel = _pending_cancel_order_from_earlier_conversation()
    result, create_mock = _handle_refund(existing_cancel_order=earlier_cancel)

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"
    assert create_mock.await_args.kwargs["order_id"] == _ORDER
    assert result["staged"]["success"] is True
    # The false "covers the refund" short-circuit must never fire here.
    assert "ALREADY COVERS" not in result["action_context"]
    assert "already covers" not in result["action_context"].lower()


# ── 3. Explicit cancellation on an eligible/unfulfilled order → unchanged ──

def test_cancellation_request_on_unfulfilled_order_stages_cancel_order_unchanged():
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id=_ORDER, raw_address=None, confidence=0.9)
    unfulfilled_eligibility = {
        "eligible": False,
        "order": {"fulfillment_status": None, "created_at": "2026-09-01T00:00:00-04:00"},
        "reason": "This order hasn't shipped yet.",
    }
    create_mock = AsyncMock(return_value={"success": True, "action_id": "cancel-new"})

    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=unfulfilled_eligibility)), \
         patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock(return_value=None)):
        result = _run(integration.handle_return_intent(
            query=f"Please cancel order #{_ORDER}",
            customer_info={"name": _CUSTOMER_NAME, "email": _CUSTOMER_EMAIL},
            existing_tool_results={}, tenant_id=_TENANT, brand_id="brand-1",
            ticket_id="ticket-cancel-now", intent_result=intent,
        ))

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "cancel_order"
    assert result["staged"]["success"] is True


# ── 4. Fulfilled order + cancellation request → existing safe behavior ─────
#      unchanged (a fulfilled order can only ever be staged as "refund",
#      never "cancel_order" - pre-existing rule, untouched by this fix)

def test_cancellation_request_on_fulfilled_order_still_stages_refund_not_cancel_order():
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="cancel", order_id=_ORDER, raw_address=None, confidence=0.9)
    create_mock = AsyncMock(return_value={"success": True, "action_id": "refund-from-cancel-intent"})

    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=None)), \
         patch.object(integration, "_create_action", new=create_mock), \
         patch.object(integration.actions, "check_return_eligibility",
                       new=AsyncMock(return_value=_fulfilled_order_eligibility())), \
         patch.object(integration, "_maybe_autopilot_refund", new=AsyncMock(return_value=None)):
        result = _run(integration.handle_return_intent(
            query=f"Please cancel order #{_ORDER}",
            customer_info={"name": _CUSTOMER_NAME, "email": _CUSTOMER_EMAIL},
            existing_tool_results={}, tenant_id=_TENANT, brand_id="brand-1",
            ticket_id="ticket-cancel-fulfilled", intent_result=intent,
        ))

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"
    assert result["staged"]["success"] is True


# ── 5. Customer-facing response never calls it a cancellation ──────────────

def test_refund_response_never_refers_to_the_request_as_a_cancellation():
    earlier_cancel = _pending_cancel_order_from_earlier_conversation()
    result, _create_mock = _handle_refund(existing_cancel_order=earlier_cancel)

    text = result["action_context"].lower()
    assert "cancel" not in text
    assert "refund" in text


# ── 6. A previous cancellation conversation cannot contaminate a new,
#      explicit refund request ──────────────────────────────────────────────

def test_previous_cancellation_conversation_does_not_contaminate_new_refund_action_metadata():
    earlier_cancel = _pending_cancel_order_from_earlier_conversation(
        ticket_id="EARLIER-UNRELATED-TICKET",
    )
    result, create_mock = _handle_refund(
        existing_cancel_order=earlier_cancel,
        ticket_id="BRAND-NEW-TICKET",
        customer_email=_CUSTOMER_EMAIL, customer_name=_CUSTOMER_NAME,
    )

    create_mock.assert_awaited_once()
    kwargs = create_mock.await_args.kwargs
    assert kwargs["ticket_id"] == "BRAND-NEW-TICKET"
    assert kwargs["ticket_id"] != "EARLIER-UNRELATED-TICKET"
    assert kwargs["action_type"] == "refund"
    assert "duplicate_of_existing_action" not in result
    assert result["staged"]["success"] is True


# ── 7. Approving the refund action dispatches to refund execution,
#      never cancellation ───────────────────────────────────────────────────

def test_approving_a_refund_action_dispatches_to_process_refund_not_cancel_order():
    from src.services.actions_service import ActionsService

    service = ActionsService()
    action_row = {
        "id": "action-refund-1", "tenant_id": _TENANT, "action_type": "refund",
        "status": "pending", "order_id": _ORDER, "ticket_id": "ticket-refund-now",
        "extracted_data": {},
    }
    fake_shopify_client = MagicMock()
    fake_shopify_client.process_refund = AsyncMock(return_value={"success": True, "refund_id": "r1"})
    fake_shopify_client.cancel_order = AsyncMock(return_value={"success": True})

    with patch.object(service, "get_action", new=AsyncMock(return_value=action_row)), \
         patch("src.services.actions_service.supabase_update", return_value={"id": "action-refund-1"}), \
         patch("src.services.actions_service.shopify_service.get_client_for_tenant",
               new=AsyncMock(return_value=fake_shopify_client)), \
         patch.object(service, "_record_edit_tracking", new=AsyncMock()):
        try:
            _run(service.approve_action(_TENANT, "action-refund-1", approved_by="owner@example.com"))
        except Exception:
            # Post-execution bookkeeping this test doesn't set up (audit
            # logging, ticket updates, notifications) may raise - the only
            # thing under test is which Shopify method got dispatched to,
            # already asserted below regardless of what happens after it.
            pass

    fake_shopify_client.process_refund.assert_awaited_once()
    fake_shopify_client.cancel_order.assert_not_awaited()
    assert fake_shopify_client.process_refund.await_args.kwargs["order_id"] == _ORDER


# ── 8. Existing partial-refund behavior remains intact ─────────────────────

def test_partial_refund_amount_still_extracted_when_staging_via_this_path():
    earlier_cancel = _pending_cancel_order_from_earlier_conversation()
    result, create_mock = _handle_refund(
        existing_cancel_order=earlier_cancel,
        query=f"Can I get a $5 refund for order #{_ORDER}? The rest is fine.",
    )

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"
    assert create_mock.await_args.kwargs.get("requested_amount") == 5.0
    assert result["staged"]["success"] is True


# ── The exact real-world reported message ───────────────────────────────────

def test_exact_reported_message_stages_refund_never_cancellation():
    earlier_cancel = _pending_cancel_order_from_earlier_conversation()
    result, create_mock = _handle_refund(
        existing_cancel_order=earlier_cancel, query=_REAL_WORLD_MESSAGE,
    )

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["action_type"] == "refund"
    text = result["action_context"].lower()
    assert "cancel" not in text
    assert "already covers" not in text
    assert result["staged"]["success"] is True
