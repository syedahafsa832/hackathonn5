"""
Production-safety fix: tResolv must never tell a customer their order was
cancelled without Shopify's cancel_order mutation actually having succeeded.

Traced the existing cancellation specialist/action/Shopify integration
(cancellation_specialist.py, return_actions_integration.py, actions_service.py,
shopify_service.py) and confirmed the safety invariant already holds at every
layer EXCEPT one gap, fixed here:

  actions_manager.check_return_eligibility()'s generic Shopify-lookup
  exception handler returned eligible=False with no requires_manual_review/
  staging_required flags - unlike its sibling "order not found" branch. That
  routed a genuine Shopify API failure into the weak generic "NOT ELIGIBLE...
  offer to escalate if frustrated" reply in return_actions_integration.py
  instead of the existing deterministic human-review staging path. Fixed by
  setting the same two flags the "order not found" branch already sets, so
  a real API failure fails closed exactly like an unconfirmed order does.

Everything else this suite checks (fulfilled -> refund fallback with no
"cancelled" claim, and the Shopify-call-result gate in actions_service.py's
approve_action) was already correctly enforced in code before this session -
these are regression tests, not new behavior, except test 3.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.actions_manager import ActionsManager  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.actions_service import actions_service  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _cancel_action(**overrides):
    a = {
        "id": "action-1", "tenant_id": "tenant-1", "brand_id": "brand-1",
        "ticket_id": "ticket-1", "status": "pending", "action_type": "cancel_order",
        "order_id": "1013", "customer_email": "jane@example.com",
        "extracted_data": {},
    }
    a.update(overrides)
    return a


# 1. Unfulfilled order -> existing cancellation flow remains valid.
def test_unfulfilled_order_still_stages_cancel_order_and_never_refund():
    integration = ReturnActionsIntegration()

    async def _no_active(tenant_id, oid, action_type):
        return None

    create_action = AsyncMock(return_value={
        "success": True, "action_id": "action-1", "action_type": "cancel_order",
        "status": "pending", "risk_level": "low",
    })
    with patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
             "eligible": False, "order": {"fulfillment_status": "unfulfilled", "id": "1013"},
             "items": [], "reason": None,
         })), \
         patch.object(integration, "_find_active_action", new=_no_active), \
         patch("src.services.return_actions_integration.supabase_select", return_value=[{"id": "brand-1"}]), \
         patch("src.services.actions_service.actions_service.create_action", new=create_action), \
         patch("src.services.actions_service.actions_service.approve_action", new=AsyncMock()) as mock_approve:
        result = run(integration.handle_return_intent(
            query="cancel order #1013", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
        ))

    assert result["staged"]["action_type"] == "cancel_order"
    create_action.assert_awaited_once()
    assert create_action.call_args.kwargs.get("action_type") == "cancel_order"
    mock_approve.assert_not_called()  # human approval still required, no Autopilot configured
    assert "cancelled successfully" not in result["action_context"].lower()


# 2. Fulfilled/shipped order -> no cancel_order action staged, escalates instead.
def test_fulfilled_order_never_stages_cancel_and_never_claims_cancellation():
    integration = ReturnActionsIntegration()

    async def _no_active(tenant_id, oid, action_type):
        return None

    create_action = AsyncMock(return_value={
        "success": True, "action_id": "action-2", "action_type": "refund",
        "status": "pending", "risk_level": "low",
    })
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
             "eligible": False, "order": {"fulfillment_status": "fulfilled", "id": "1013"},
             "items": [], "reason": "order already fulfilled", "staging_required": True,
         })), \
         patch.object(integration, "_find_active_action", new=_no_active), \
         patch("src.services.return_actions_integration.supabase_select", return_value=[{"id": "brand-1"}]), \
         patch("src.services.actions_service.actions_service.create_action", new=create_action), \
         patch("src.services.actions_service.actions_service.approve_action", new=AsyncMock()) as mock_approve:
        result = run(integration.handle_return_intent(
            query="cancel order #1013", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
        ))

    # A cancel_order action must never be staged for a fulfilled order.
    staged_type = create_action.call_args.kwargs.get("action_type") if create_action.call_args.kwargs else None
    assert staged_type != "cancel_order"
    mock_approve.assert_not_called()
    ctx = result["action_context"].lower()
    # Must instruct the model not to claim cancellation/stopped shipment as
    # fact - never assert those claims itself.
    assert "do not say the order has been cancelled" in ctx
    assert "your order has been canceled" not in ctx
    assert "the shipment has been stopped" not in ctx
    assert "won't be shipped" not in ctx
    assert "manual review" in ctx


# 3. Unknown Shopify fulfillment state (API failure) -> fail closed, escalated.
def test_shopify_api_failure_fails_closed_and_escalates():
    manager = ActionsManager()

    async def _boom(*args, **kwargs):
        raise ConnectionError("Shopify API timed out")

    with patch.object(manager, "_get_order_from_shopify", new=_boom):
        result = run(manager.check_return_eligibility("1013", "c@example.com", tenant_id="tenant-1", brand_id="brand-1"))

    assert result["eligible"] is False
    assert result["order"] is None
    # This is the actual fix: a real lookup failure must route into the same
    # deterministic human-review staging path as a confirmed-not-found order,
    # never the weaker "not eligible, escalate if frustrated" generic reply.
    assert result["requires_manual_review"] is True
    assert result["staging_required"] is True
    assert "cancel" not in result["reason"].lower() or "cancelled" not in result["reason"].lower()


# 4. Shopify cancellation succeeds -> customer can receive confirmation.
@pytest.mark.asyncio
async def test_shopify_cancel_success_reaches_post_execution_notify():
    action = _cancel_action()
    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()) as mock_notify, \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={
            "success": True, "cancelled_at": "2026-01-01T00:00:00Z", "message": "Successfully cancelled order #1013",
        })
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com"
        )

    assert result["success"] is True
    mock_client.cancel_order.assert_awaited_once()
    mock_notify.assert_awaited_once()  # only reachable after a confirmed Shopify success


# 5. Shopify cancellation fails -> customer must NOT receive "canceled".
@pytest.mark.asyncio
async def test_shopify_cancel_failure_never_notifies_or_reports_success():
    from src.services.shopify_service import ShopifyError, ShopifyErrorCode

    action = _cancel_action()
    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()) as mock_notify, \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(side_effect=ShopifyError(
            "Cannot cancel a fulfilled order. Please process a refund instead.",
            ShopifyErrorCode.ORDER_ALREADY_FULFILLED,
        ))
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(
            tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com"
        )

    assert result["success"] is False
    mock_notify.assert_not_called()  # no customer-facing confirmation on a failed Shopify call


# 6. Existing human approval flow remains intact - a staged cancel_order
# action sits pending and is never auto-executed by staging alone.
def test_staged_cancel_order_requires_explicit_approval_call():
    integration = ReturnActionsIntegration()

    async def _no_active(tenant_id, oid, action_type):
        return None

    create_action = AsyncMock(return_value={
        "success": True, "action_id": "action-1", "action_type": "cancel_order",
        "status": "pending", "risk_level": "low",
    })
    with patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
             "eligible": False, "order": {"fulfillment_status": "unfulfilled", "id": "1013"},
             "items": [], "reason": None,
         })), \
         patch.object(integration, "_find_active_action", new=_no_active), \
         patch("src.services.return_actions_integration.supabase_select", return_value=[{"id": "brand-1"}]), \
         patch("src.services.actions_service.actions_service.create_action", new=create_action), \
         patch("src.services.actions_service.actions_service.approve_action", new=AsyncMock()) as mock_approve:
        result = run(integration.handle_return_intent(
            query="cancel order #1013", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
        ))

    assert result["staged"]["status"] == "pending"
    mock_approve.assert_not_called()
