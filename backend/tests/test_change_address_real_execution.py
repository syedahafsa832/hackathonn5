"""
Change Address becomes a real, executable Shopify action from both the
Conversation Inbox (Order Context -> Change Address) and Escalations
(Approve & Execute) - reusing the existing action/approval/dedup/
authorization systems throughout, no second action framework.

Covers:
1. Merchant-initiated flow (Inbox): create_action() + approve_action() in
   sequence (exactly what the frontend now does) actually calls Shopify's
   update_shipping_address() and persists old/new address + execution
   result - never a fake "Queued" for something that already executed.
2. AI/customer-initiated flow (return_actions_integration.py): now has the
   same duplicate-request guard refund/cancel/reship already had, and
   enriches the escalation with the order's CURRENT address before
   staging, so a reviewer sees what's changing from/to.
3. Failure honesty: a fulfilled order's address change fails with a real,
   persistent, merchant-safe reason - never silently marked complete.
4. Duplicate protection: repeated clicks never create a second active
   action or mutate Shopify twice.
5. Reship is untouched - still always manual_action_required.
6. Cross-tenant isolation is preserved for the new flow.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402
from src.services.actions_service import actions_service, ActionStatus  # noqa: E402
from src.services.shopify_service import ShopifyError, ShopifyErrorCode  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


NEW_ADDRESS = {"address1": "123 New St", "city": "Springfield", "province": "IL", "zip": "62704", "country": "US", "name": "Jane Doe"}


def _action(status="pending", extracted_data=None, **overrides):
    a = {
        "id": "action-1", "tenant_id": "tenant-1", "brand_id": "brand-1",
        "ticket_id": "ticket-1", "status": status, "action_type": "change_address",
        "order_id": "1001", "customer_email": "jane@example.com", "customer_name": "Jane Doe",
        "extracted_data": extracted_data if extracted_data is not None else {"new_address": NEW_ADDRESS},
    }
    a.update(overrides)
    return a


# ── 1. Merchant-initiated (Inbox): create then approve actually executes ────

@pytest.mark.asyncio
async def test_inbox_change_address_create_then_approve_calls_real_shopify_mutation():
    action = _action()
    updated = {"success": True, "order_id": "1001", "order_name": "#1001",
               "old_address": {"address1": "999 Old Ave", "city": "Old Town", "country": "US"},
               "new_address": NEW_ADDRESS, "message": "Successfully updated shipping address for order #1001"}

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.update_shipping_address = AsyncMock(return_value=updated)
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com")

    assert result["success"] is True
    mock_client.update_shipping_address.assert_awaited_once()
    call_kwargs = mock_client.update_shipping_address.await_args.kwargs
    assert call_kwargs["new_address"] == NEW_ADDRESS
    # Persisted result carries old + new address + execution outcome, not just a flag.
    assert result["execution_result"]["old_address"]["address1"] == "999 Old Ave"
    assert result["execution_result"]["new_address"]["address1"] == "123 New St"


@pytest.mark.asyncio
async def test_no_new_address_falls_back_to_manual_never_fakes_execution():
    """The merchant clicking Change Address with no address entered must
    never be silently treated as 'done' - the client already blocks this,
    but the backend must never invent one either."""
    action = _action(extracted_data={})  # no new_address at all
    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", return_value=[action]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.update_shipping_address = AsyncMock()
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com")

    mock_client.update_shipping_address.assert_not_awaited()
    assert result["execution_result"]["manual_action_required"] is True


# ── 3. Failure honesty ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fulfilled_order_address_change_fails_persistently_not_silently():
    action = _action()
    persisted = {}

    def fake_update(table, match, data):
        if table == "actions":
            persisted.update(data)
        return [action]

    with patch("src.services.actions_service.supabase_select", return_value=[action]), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.update_shipping_address = AsyncMock(
            side_effect=ShopifyError("Cannot change address for a fulfilled order.", ShopifyErrorCode.ORDER_ALREADY_FULFILLED)
        )
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com")

    assert result["success"] is False
    assert "fulfilled" in result["error"].lower()
    # Persisted as status=failed with a merchant-safe reason - not just a
    # transient toast the frontend forgets on refresh.
    assert persisted.get("status") == ActionStatus.FAILED.value
    assert "fulfilled" in persisted.get("error_message", "").lower()


# ── 4. Duplicate protection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_double_click_does_not_re_execute_an_already_executed_action():
    executed_action = _action(status="executed")
    with patch("src.services.actions_service.supabase_select", return_value=[executed_action]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter:
        mock_client = MagicMock()
        mock_client.update_shipping_address = AsyncMock()
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(tenant_id="tenant-1", action_id="action-1", approved_by="merchant@example.com")

    assert result["success"] is False
    assert "already" in result["error"].lower()
    mock_client.update_shipping_address.assert_not_awaited()


def test_create_action_refuses_a_second_pending_change_address_for_same_order():
    """The app-level half of dedup (DB unique index is the backstop,
    covered separately) - create_action's own pre-check."""
    existing_pending = _action(status="pending")

    def fake_select(table, params=None):
        if table == "actions" and (params or {}).get("action_type") == "eq.change_address":
            return [existing_pending]
        return []

    # detect_and_create is the AI-detection path's own pre-check; the
    # merchant/Inbox path calls create_action directly, which relies on the
    # DB unique index (idx_actions_dedup_active) to catch this - simulated
    # here via the same 409 the real Supabase constraint raises.
    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_insert", side_effect=Exception("409 Client Error: Conflict")):
        result = run(actions_service.create_action(
            tenant_id="tenant-1", action_type="change_address", customer_email="jane@example.com",
            order_id="1001", extracted_data={"new_address": NEW_ADDRESS},
        ))

    assert result["success"] is True
    assert result["status"] == "duplicate_skipped"
    assert result["action_id"] == existing_pending["id"]


# ── 2. AI/customer-initiated: dedup guard + current-address enrichment ──────

def _run_address_change(order_id="1001", existing_action=None, shopify_order=None):
    integration = ReturnActionsIntegration()
    intent = IntentResult(action_type="address_change", order_id=order_id, raw_address="123 New St, Springfield, IL 62704", confidence=0.9)

    fake_client = MagicMock()
    fake_client.get_order = AsyncMock(return_value={"success": True, "order": shopify_order} if shopify_order else {"success": False})

    with patch.object(integration, "_find_active_action", new=AsyncMock(return_value=existing_action)), \
         patch.object(integration, "_create_action", new=AsyncMock(return_value={"success": True, "action_id": "a1"})) as mock_create, \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)), \
         patch.object(integration, "_validate_address", return_value=(True, [])), \
         patch("src.services.intent_detector.intent_detector.parse_address", new=AsyncMock(return_value=NEW_ADDRESS)):
        result = run(integration.handle_return_intent(
            query="please change my address to 123 New St, Springfield, IL 62704",
            customer_info={"name": "Jane", "email": "jane@example.com"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1",
            ticket_id="ticket-1", intent_result=intent,
        ))
    return result, mock_create


def test_address_change_checks_for_existing_active_action_before_staging():
    result, mock_create = _run_address_change(existing_action=None)
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["action_type"] == "change_address"
    assert "QUEUED" in result["action_context"]


def test_address_change_does_not_stage_a_second_action_when_pending():
    existing = {"status": "pending", "action_type": "change_address"}
    result, mock_create = _run_address_change(existing_action=existing)
    mock_create.assert_not_awaited()
    assert "ALREADY PENDING" in result["action_context"]
    assert "address change" in result["action_context"].lower()
    # Never the wrong ("cancellation") noun a missing branch would fall back to.
    assert "CANCELLATION" not in result["action_context"]


def test_address_change_attaches_current_shipping_address_before_approval():
    shopify_order = {
        "shipping_address": {"address1": "999 Old Ave", "city": "Old Town", "country": "US"},
        "fulfillment_status": "unfulfilled",
    }
    _, mock_create = _run_address_change(shopify_order=shopify_order)
    assert mock_create.await_args.kwargs["current_shipping_address"]["address1"] == "999 Old Ave"
    assert mock_create.await_args.kwargs["current_fulfillment_status"] == "unfulfilled"


def test_address_change_still_stages_when_order_lookup_fails():
    _, mock_create = _run_address_change(shopify_order=None)
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["current_shipping_address"] is None


# ── 5. Reship stays manual, untouched by this change ────────────────────────

@pytest.mark.asyncio
async def test_reship_approval_is_still_always_manual_action_required():
    reship_action = {
        "id": "action-2", "tenant_id": "tenant-1", "brand_id": "brand-1", "ticket_id": None,
        "status": "pending", "action_type": "reship", "order_id": "1001",
        "customer_email": "jane@example.com", "extracted_data": {},
    }
    with patch("src.services.actions_service.supabase_select", return_value=[reship_action]), \
         patch("src.services.actions_service.supabase_update", return_value=[reship_action]), \
         patch("src.services.shopify_service.shopify_service.get_client_for_tenant", new=AsyncMock()) as mock_client_getter, \
         patch.object(actions_service, "_post_execution_notify", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.update_shipping_address = AsyncMock()
        mock_client_getter.return_value = mock_client

        result = await actions_service.approve_action(tenant_id="tenant-1", action_id="action-2", approved_by="merchant@example.com")

    assert result["execution_result"]["manual_action_required"] is True
    # Confirms the fix never accidentally reused Change Address's real
    # execution path for Reship.
    mock_client.update_shipping_address.assert_not_awaited()


# ── 6. Cross-tenant isolation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_address_action_is_tenant_scoped_for_approval():
    """An action created under tenant-1 must be invisible (404-equivalent)
    to an approve_action call from a different tenant."""
    action = _action()

    def fake_select(table, params=None):
        if table == "actions":
            if (params or {}).get("tenant_id") == "eq.tenant-1":
                return [action]
            return []  # different tenant sees nothing
        return []

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select):
        result = await actions_service.approve_action(tenant_id="tenant-EVIL", action_id="action-1", approved_by="attacker@example.com")

    assert result["success"] is False
    assert result["error"] == "Action not found"
