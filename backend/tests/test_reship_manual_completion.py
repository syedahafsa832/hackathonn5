"""
Reship truthful-completion fix.

Reship is manual: approve_action()'s RESHIP branch never calls Shopify. It
previously still marked the action 'executed' anyway, identical to a real
automated action - misleading a merchant into thinking the replacement
shipment existed. Fixed: RESHIP approval now lands in a distinct
'awaiting_manual_step' status, and only actions_service.complete_manual_action()
(triggered by the merchant's explicit "Mark Reship Complete") ever moves it
to 'executed'. No other action type's approve_action branch changed.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.actions_service import actions_service, ActionStatus  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


TENANT = "tenant-1"


def _pending_action(action_type="reship", order_id="1005", **overrides):
    a = {
        "id": "action-1", "tenant_id": TENANT, "action_type": action_type,
        "status": "pending", "order_id": order_id, "customer_email": "jane@example.com",
        "customer_name": "Jane", "extracted_data": {}, "brand_id": "brand-1",
    }
    a.update(overrides)
    return a


def _approve(action_type="reship", order_id="1005", action_overrides=None):
    action = _pending_action(action_type=action_type, order_id=order_id, **(action_overrides or {}))
    shopify_client = MagicMock()
    shopify_client.process_refund = AsyncMock(return_value={"success": True, "amount": 10, "order_name": "#1005"})
    shopify_client.cancel_order = AsyncMock(return_value={"success": True, "order_name": "#1005"})

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        return []

    updates = []

    def fake_update(table, match, data):
        updates.append((table, match, data))
        if table == "actions":
            action.update(data)
            return data
        return {}

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.actions_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=shopify_client)), \
         patch("src.services.actions_service.actions_service._post_execution_notify", new=AsyncMock()):
        result = run(actions_service.approve_action(tenant_id=TENANT, action_id="action-1", approved_by="owner@example.com"))
    return result, action, shopify_client, updates


# ── 1-4. Reship approval state machine ──────────────────────────────────────

def test_reship_creation_stores_the_selected_order_id():
    from src.api.routes.saas_actions import CreateActionRequest  # validates the field exists/required shape
    with patch("src.services.actions_service.supabase_insert", return_value={"id": "a1"}) as mock_insert, \
         patch.object(actions_service, "_calculate_risk", new=AsyncMock(return_value=("low", []))), \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        run(actions_service.create_action(
            tenant_id=TENANT, action_type="reship", customer_email="jane@example.com",
            order_id="1005", ticket_id="ticket-1",
        ))
    assert mock_insert.call_args[0][1]["order_id"] == "1005"


def test_reship_approval_does_not_call_shopify():
    _, _, shopify_client, _ = _approve()
    shopify_client.process_refund.assert_not_awaited()
    shopify_client.cancel_order.assert_not_awaited()


def test_reship_approval_does_not_mark_executed():
    result, action, _, _ = _approve()
    assert result["success"] is True
    assert action["status"] != ActionStatus.EXECUTED.value


def test_reship_approval_produces_awaiting_manual_step_status():
    result, action, _, _ = _approve()
    assert action["status"] == ActionStatus.AWAITING_MANUAL_STEP.value
    assert action.get("execution_result", {}).get("manual_action_required") is True
    assert "executed_at" not in [k for k, v in action.items() if k == "executed_at" and v]


# ── 5. Visibility after refresh (via /history) ──────────────────────────────

def test_awaiting_manual_step_reship_appears_in_history():
    with patch("src.services.actions_service.supabase_select", return_value=[
        {"id": "action-1", "status": "awaiting_manual_step", "action_type": "reship", "order_id": "1005"},
    ]) as mock_select:
        history = run(actions_service.get_action_history(TENANT))
    assert any(a["id"] == "action-1" for a in history)
    assert "awaiting_manual_step" in mock_select.call_args[0][1]["status"]


# ── 6-9. Explicit completion ─────────────────────────────────────────────────

def _complete(action, completed_by="owner@example.com"):
    def fake_select(table, params=None):
        if table == "actions":
            # Simulate real row-level filtering: only visible when the
            # query's tenant_id filter matches this row's actual tenant_id.
            if params.get("tenant_id") != f"eq.{action['tenant_id']}":
                return []
            return [action]
        return []

    updates = []

    def fake_update(table, match, data):
        # Simulate the conditional WHERE clause: only "succeeds" if the
        # match's status filter matches the row's current status.
        if "status" in match and match["status"] != f"eq.{action['status']}":
            return {}
        updates.append(data)
        action.update(data)
        return data

    with patch("src.services.actions_service.supabase_select", side_effect=fake_select), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch.object(actions_service, "_log_event", new=AsyncMock()):
        result = run(actions_service.complete_manual_action(tenant_id=TENANT, action_id=action["id"], completed_by=completed_by))
    return result, updates


def test_merchant_can_mark_awaiting_reship_complete():
    action = {"id": "action-1", "tenant_id": TENANT, "status": "awaiting_manual_step", "action_type": "reship",
              "order_id": "1005", "execution_result": {"manual_action_required": True, "message": "Please create a replacement shipment in Shopify admin for this order."}}
    result, _ = _complete(action)
    assert result["success"] is True
    assert result["status"] == "executed"


def test_completion_moves_action_to_executed_and_clears_manual_flag():
    action = {"id": "action-1", "tenant_id": TENANT, "status": "awaiting_manual_step", "action_type": "reship",
              "order_id": "1005", "execution_result": {"manual_action_required": True, "message": "orig"}}
    _complete(action)
    assert action["status"] == "executed"
    assert action["execution_result"]["manual_action_required"] is False
    assert action["execution_result"]["message"] == "orig"  # original instruction preserved
    assert action["execution_result"]["manually_completed_by"] == "owner@example.com"
    assert "manually_completed_at" in action["execution_result"]


def test_completion_never_calls_shopify():
    action = {"id": "action-1", "tenant_id": TENANT, "status": "awaiting_manual_step", "action_type": "reship",
              "order_id": "1005", "execution_result": {"manual_action_required": True}}
    with patch("src.services.actions_service.shopify_service.get_client_for_tenant") as mock_get_client:
        _complete(action)
    mock_get_client.assert_not_called()


def test_completion_is_idempotent():
    action = {"id": "action-1", "tenant_id": TENANT, "status": "awaiting_manual_step", "action_type": "reship",
              "order_id": "1005", "execution_result": {"manual_action_required": True}}
    first, _ = _complete(action)
    second, _ = _complete(action)  # action is now already "executed"
    assert first["success"] is True
    assert second["success"] is True
    assert second.get("already_completed") is True


# ── 10. Tenant isolation ─────────────────────────────────────────────────────

def test_unauthorized_tenant_cannot_complete_another_tenants_reship():
    action = {"id": "action-1", "tenant_id": "tenant-OTHER", "status": "awaiting_manual_step",
              "action_type": "reship", "order_id": "1005", "execution_result": {"manual_action_required": True}}
    result, updates = _complete(action, completed_by="attacker@example.com")
    assert result["success"] is False
    assert action["status"] == "awaiting_manual_step"  # unchanged
    assert updates == []


# ── 11-13. Other action types unaffected ────────────────────────────────────

def test_refund_approval_still_marks_executed_and_calls_shopify():
    result, action, shopify_client, _ = _approve(action_type="refund")
    shopify_client.process_refund.assert_awaited_once()
    assert action["status"] == ActionStatus.EXECUTED.value
    assert "executed_at" in action


def test_cancel_order_approval_still_marks_executed_and_calls_shopify():
    result, action, shopify_client, _ = _approve(action_type="cancel_order")
    shopify_client.cancel_order.assert_awaited_once()
    assert action["status"] == ActionStatus.EXECUTED.value


def test_change_address_manual_branch_still_marks_executed_not_awaiting():
    """Change Address's own manual_action_required case (no structured
    address) is explicitly out of scope for this fix - it must keep going
    straight to EXECUTED, unlike reship."""
    result, action, _, _ = _approve(action_type="change_address", action_overrides={"extracted_data": {}})
    assert action["status"] == ActionStatus.EXECUTED.value
    assert action["execution_result"]["manual_action_required"] is True


# ── 14. Duplicate prevention (dedup lists include the new status) ──────────

def test_duplicate_lookup_after_db_conflict_includes_awaiting_manual_step():
    with patch("src.services.actions_service.supabase_insert", side_effect=Exception("409 Conflict")), \
         patch("src.services.actions_service.supabase_select", return_value=[{"id": "existing-1"}]) as mock_select, \
         patch.object(actions_service, "_calculate_risk", new=AsyncMock(return_value=("low", []))):
        result = run(actions_service.create_action(
            tenant_id=TENANT, action_type="reship", customer_email="jane@example.com", order_id="1005",
        ))
    assert result["status"] == "duplicate_skipped"
    assert "awaiting_manual_step" in mock_select.call_args[0][1]["status"]
