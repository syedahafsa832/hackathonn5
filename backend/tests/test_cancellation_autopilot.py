"""
tResolv — Phase 4: Cancellation Autopilot Execution.

Two surfaces are covered:

A. POST /{brand_id}/automation/cancellation/{enable,disable} (v2_brands.py)
   - the ONLY way the cancellation_autopilot_enabled flag can be flipped,
     re-verifying readiness/entitlement/ownership server-side every time,
     never trusting a frontend toggle.

B. ReturnActionsIntegration._maybe_autopilot_cancel /
   handle_return_intent's CANCEL QUEUED branch (return_actions_integration.py)
   - the single hook point where an already-fully-eligible cancellation may
     be auto-executed, exclusively via actions_service.approve_action() (the
     same function a human's Approve click calls) - no second execution
     path, no execution decided by the model.

Numbered comments map back to the task's 18 named test scenarios.
"""
import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ══════════════════════════════════════════════════════════════════════════
# A. Activation / kill-switch endpoints
# ══════════════════════════════════════════════════════════════════════════

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND = {
    "id": "brand-1", "tenant_id": "tenant-1", "name": "Test Brand",
    "shopify_connected": True, "cancellation_autopilot_enabled": False,
}


def _override_tenant(tenant_id="tenant-1"):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def _with_tenant(fn, tenant_id="tenant-1"):
    app.dependency_overrides[get_current_tenant] = _override_tenant(tenant_id)
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


def _ready_cancel_actions():
    # 5 executed, 0 failed -> ready_for_review (matches _AUTOPILOT_MIN_SAMPLE=5)
    return [{"id": f"a{i}", "action_type": "cancel_order", "status": "executed"} for i in range(5)]


def _not_ready_cancel_actions():
    return [{"id": "a1", "action_type": "cancel_order", "status": "executed"}]


# 1. Merchant cannot enable Autopilot when readiness is insufficient.
def test_enable_blocked_when_readiness_insufficient():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return _not_ready_cancel_actions()
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update:
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/cancellation/enable"))

    assert resp.status_code == 400
    assert "not_ready" in resp.json()["detail"] or "isn't ready" in resp.json()["detail"]
    mock_update.assert_not_called()


# 2. Merchant can enable when readiness is sufficient.
def test_enable_succeeds_when_readiness_sufficient():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return _ready_cancel_actions()
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update", return_value={}) as mock_update, \
         patch("src.services.plan_service.check_limit", return_value={"allowed": True}):
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/cancellation/enable"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["enabled"] is True
    assert body["readiness"]["status"] == "ready_for_review"
    mock_update.assert_called_once()
    call_args = mock_update.call_args
    assert call_args[0][0] == "brands"
    assert call_args[0][2]["cancellation_autopilot_enabled"] is True


# 3. Unauthorized user cannot enable it.
def test_enable_requires_authentication():
    # No dependency override, no Authorization header -> the real
    # get_current_tenant dependency runs and rejects the request.
    resp = client.post("/api/v2/brands/brand-1/automation/cancellation/enable")
    assert resp.status_code == 401


# 4. Wrong tenant cannot enable it.
def test_enable_blocked_for_wrong_tenant():
    def fake_select(table, params=None):
        if table == "brands":
            # brand-1 is owned by tenant-1; querying with tenant_id filter
            # for a different tenant must return nothing (real PostgREST
            # semantics - _get_owned_brand's own query includes the filter).
            if params and params.get("tenant_id") == "eq.tenant-attacker":
                return []
            return [BRAND]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update:
        resp = _with_tenant(
            lambda: client.post("/api/v2/brands/brand-1/automation/cancellation/enable"),
            tenant_id="tenant-attacker",
        )

    assert resp.status_code == 404
    mock_update.assert_not_called()


def test_enable_blocked_without_shopify_connection():
    brand = {**BRAND, "shopify_connected": False}

    def fake_select(table, params=None):
        if table == "brands":
            return [brand]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update:
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/cancellation/enable"))

    assert resp.status_code == 400
    assert "Shopify" in resp.json()["detail"]
    mock_update.assert_not_called()


def test_enable_blocked_when_not_entitled():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return _ready_cancel_actions()
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update, \
         patch("src.services.plan_service.check_limit", return_value={"allowed": False, "used": 50, "limit": 50}):
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/cancellation/enable"))

    assert resp.status_code == 402
    mock_update.assert_not_called()


# 16 (endpoint half). Turning Autopilot off requires only auth+ownership -
# no readiness gate, always allowed, always immediate.
def test_disable_always_allowed_regardless_of_readiness():
    brand_on = {**BRAND, "cancellation_autopilot_enabled": True}

    def fake_select(table, params=None):
        if table == "brands":
            return [brand_on]
        if table == "actions":
            return _not_ready_cancel_actions()  # readiness irrelevant to disable
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update", return_value={}) as mock_update:
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/cancellation/disable"))

    assert resp.status_code == 200
    assert resp.json() == {"success": True, "enabled": False}
    call_args = mock_update.call_args
    assert call_args[0][2]["cancellation_autopilot_enabled"] is False


# 18 (endpoint half). Tenant isolation - wrong tenant cannot disable another
# tenant's brand either.
def test_disable_blocked_for_wrong_tenant():
    def fake_select(table, params=None):
        if table == "brands":
            if params and params.get("tenant_id") == "eq.tenant-attacker":
                return []
            return [BRAND]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update:
        resp = _with_tenant(
            lambda: client.post("/api/v2/brands/brand-1/automation/cancellation/disable"),
            tenant_id="tenant-attacker",
        )
    assert resp.status_code == 404
    mock_update.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════
# B. Autopilot execution hook (return_actions_integration.py)
# ══════════════════════════════════════════════════════════════════════════

def _integration():
    return ReturnActionsIntegration()


def _eligibility(order_id="1013", fulfillment_status=None, eligible=False):
    return {
        "eligible": eligible,
        "order": {"fulfillment_status": fulfillment_status, "id": order_id},
        "items": [],
        "order_total": 50.0,
        "reason": None,
    }


def _common_patches(integration, brand_row, autopilot_outcome=None, create_action_result=None):
    """Shared mocking for handle_return_intent's cancel-path: no existing
    active action (fresh request), a real Shopify order lookup, no custom
    policy text, action creation succeeds, and (if provided) an
    approve_action outcome for the autopilot attempt."""
    patches = []
    patches.append(patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")))
    patches.append(patch("src.services.return_actions_integration.supabase_select", side_effect=lambda table, params=None: (
        [brand_row] if table == "brands" else []
    )))
    if create_action_result is None:
        create_action_result = {"success": True, "action_id": "action-1", "action_type": "cancel_order", "status": "pending", "risk_level": "low"}
    patches.append(patch("src.services.actions_service.actions_service.create_action", new=AsyncMock(return_value=create_action_result)))
    mock_approve = AsyncMock(return_value=autopilot_outcome or {"success": True, "message": "cancelled"})
    patches.append(patch("src.services.actions_service.actions_service.approve_action", new=mock_approve))
    return patches, mock_approve


def _run_cancel_intent(integration, brand_row, autopilot_outcome=None, order_id="1013", find_active=None):
    async def _fake_find_active(tenant_id, oid, action_type):
        return find_active
    patches, mock_approve = _common_patches(integration, brand_row, autopilot_outcome=autopilot_outcome)
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(
        return_value=_eligibility(order_id=order_id, fulfillment_status="unfulfilled", eligible=False)
    )), patch.object(integration, "_find_active_action", new=_fake_find_active), \
         patches[0], patches[1], patches[2], patches[3]:
        result = run(integration.handle_return_intent(
            query=f"cancel order #{order_id}",
            customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={},
            tenant_id="tenant-1",
            brand_id="brand-1",
            ticket_id="ticket-1",
        ))
    return result, mock_approve


# 5. Disabled Autopilot never automatically executes cancellation.
def test_disabled_autopilot_never_auto_executes():
    integration = _integration()
    brand_off = {"id": "brand-1", "cancellation_autopilot_enabled": False}
    result, mock_approve = _run_cancel_intent(integration, brand_off)

    mock_approve.assert_not_called()
    assert "CANCEL QUEUED" in result["action_context"]
    assert "cancelled successfully" not in result["action_context"].lower()


# 6 / 17. Enabled Autopilot executes an eligible cancellation exactly once,
# and produces a proper action/audit record via actions_service.approve_action
# (the same function human approval calls - no second execution path).
def test_enabled_autopilot_executes_eligible_cancellation_exactly_once():
    integration = _integration()
    brand_on = {"id": "brand-1", "cancellation_autopilot_enabled": True}
    result, mock_approve = _run_cancel_intent(
        integration, brand_on, autopilot_outcome={"success": True, "message": "Order cancelled"}, order_id="1013",
    )

    mock_approve.assert_called_once()
    _, kwargs = mock_approve.call_args
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["action_id"] == "action-1"
    assert kwargs["approved_by"] == "autopilot"
    assert kwargs["idempotency_key"] == "autopilot-action-1"
    assert "CANCEL COMPLETED AUTOMATICALLY" in result["action_context"]
    assert "cancelled successfully" in result["action_context"]
    assert "#1013" in result["action_context"]


# 7. Duplicate requests cannot execute twice - the existing duplicate-action
# guard (checked before eligibility/autopilot are ever reached) short-
# circuits a second request for the same order, so autopilot never gets a
# second action to auto-approve.
def test_duplicate_request_short_circuits_before_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "cancellation_autopilot_enabled": True}
    existing = {"id": "action-1", "status": "executed", "action_type": "cancel_order", "order_id": "1013"}

    result, mock_approve = _run_cancel_intent(integration, brand_on, find_active=existing)

    mock_approve.assert_not_called()
    assert "staged" not in result or result.get("staged") is None


# 8 / 12. Ineligible (fulfilled/shipped) order escalates instead of being
# auto-cancelled - the CANCEL QUEUED branch (and therefore autopilot) is
# structurally unreachable for a fulfilled order; it stages via the
# existing "NOT ELIGIBLE" manual-review path, unchanged.
def test_fulfilled_order_escalates_never_reaches_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "cancellation_autopilot_enabled": True}
    patches, mock_approve = _common_patches(integration, brand_on)

    async def _no_active(tenant_id, oid, action_type):
        return None

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
        "eligible": False, "order": {"fulfillment_status": "fulfilled", "id": "1013"},
        "items": [], "order_total": 50.0, "reason": "Return window has closed",
        "staging_required": True,
    })), patch.object(integration, "_find_active_action", new=_no_active), \
         patches[0], patches[1], patches[2], patches[3]:
        result = run(integration.handle_return_intent(
            query="cancel order #1013", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
        ))

    mock_approve.assert_not_called()
    assert "MANUAL REVIEW" in result["action_context"]


# 9 / 10. Shopify failure during an automatic attempt never produces
# success messaging and escalates correctly - truthful wording, no
# fabricated response-time promise.
def test_autopilot_shopify_failure_escalates_without_claiming_success():
    integration = _integration()
    brand_on = {"id": "brand-1", "cancellation_autopilot_enabled": True}
    result, mock_approve = _run_cancel_intent(
        integration, brand_on,
        autopilot_outcome={"success": False, "error": "Shopify rejected the cancellation: order already partially shipped"},
        order_id="1013",
    )

    mock_approve.assert_called_once()
    ctx = result["action_context"]
    assert "ESCALATED" in ctx
    assert "cancelled successfully" not in ctx.lower()
    assert "Done!" not in ctx
    assert "couldn't complete the cancellation automatically" in ctx
    assert "sent this to our team for review" in ctx
    # No fabricated specific response-time promise.
    assert "within 2 hours" not in ctx
    assert "shortly" not in ctx.lower()


# 11. Policy failure (merchant free-text cancellation policy present)
# escalates before ever attempting automatic execution, even with
# Autopilot enabled - this hard eligibility rule is checked before the
# CANCEL QUEUED branch autopilot hooks into.
def test_custom_policy_text_escalates_even_with_autopilot_enabled():
    integration = _integration()
    brand_on = {"id": "brand-1", "cancellation_autopilot_enabled": True}

    async def _no_active(tenant_id, oid, action_type):
        return None

    with patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(
        return_value="Orders may only be cancelled within 1 hour of purchase."
    )), patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value=_eligibility(
        order_id="1013", fulfillment_status="unfulfilled", eligible=False,
    ))), patch.object(integration, "_find_active_action", new=_no_active), \
         patch("src.services.return_actions_integration.supabase_select", side_effect=lambda t, p=None: [brand_on] if t == "brands" else []), \
         patch("src.services.actions_service.actions_service.create_action", new=AsyncMock(return_value={
             "success": True, "action_id": "action-1", "action_type": "cancel_order", "status": "pending", "risk_level": "low",
         })), \
         patch("src.services.actions_service.actions_service.approve_action", new=AsyncMock()) as mock_approve:
        result = run(integration.handle_return_intent(
            query="cancel order #1013", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
        ))

    mock_approve.assert_not_called()
    assert "MANUAL REVIEW" in result["action_context"]


# 13. Existing Copilot behavior is byte-for-byte unchanged when the brand
# has never enabled Autopilot (column absent, same as pre-migration state -
# supabase_select's plain SELECT * returns it as simply missing/falsy).
def test_copilot_behavior_unchanged_without_autopilot_column():
    integration = _integration()
    brand_pre_migration = {"id": "brand-1"}  # no cancellation_autopilot_enabled key at all
    result, mock_approve = _run_cancel_intent(integration, brand_pre_migration, order_id="1013")

    mock_approve.assert_not_called()
    assert result["action_context"] == (
        "**CANCEL QUEUED**: Order hasn't shipped yet — cancel + refund is the right action. "
        "Tell the customer: 'Since your order hasn't shipped yet, I've sent your cancellation request "
        "to our team. They'll cancel it and your refund will appear within 3–5 business days.'"
    )


# 14. Refunds remain human-approved regardless of Cancellation Autopilot -
# the generic "not eligible" manual-review branch (shared by refund-type
# requests) never calls the autopilot hook, which only exists on the
# cancel_order-specific unfulfilled branch.
def test_refund_action_never_touches_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "cancellation_autopilot_enabled": True}

    async def _no_active(tenant_id, oid, action_type):
        return None

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
        "eligible": False, "order": {"fulfillment_status": "fulfilled", "id": "1020"},
        "items": [], "order_total": 80.0, "reason": "Item is final sale", "staging_required": True,
    })), patch.object(integration, "_find_active_action", new=_no_active), \
         patch("src.services.return_actions_integration.supabase_select", side_effect=lambda t, p=None: [brand_on] if t == "brands" else []), \
         patch("src.services.actions_service.actions_service.create_action", new=AsyncMock(return_value={
             "success": True, "action_id": "action-2", "action_type": "refund", "status": "pending", "risk_level": "low",
         })), \
         patch("src.services.actions_service.actions_service.approve_action", new=AsyncMock()) as mock_approve, \
         patch.object(integration, "_maybe_autopilot_cancel", new=AsyncMock()) as mock_autopilot_hook:
        result = run(integration.handle_return_intent(
            query="refund order #1020", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
        ))

    mock_approve.assert_not_called()
    mock_autopilot_hook.assert_not_called()
    assert "MANUAL REVIEW" in result["action_context"]


# 15. Exchanges remain human-approved - the exchange code path never
# references the cancellation autopilot hook at all.
def test_exchange_handling_never_references_autopilot_hook():
    import inspect
    from src.services import return_actions_integration as rai_module
    source = inspect.getsource(rai_module.ReturnActionsIntegration)
    # The only call site of _maybe_autopilot_cancel must be inside the
    # cancel_order-specific unfulfilled branch - structurally proves
    # exchange handling (a separate code region entirely) can never reach it.
    assert source.count("_maybe_autopilot_cancel(") == 2  # def + the one call site


# 16. Turning Autopilot off prevents new automatic executions immediately -
# the very next request for a *different* order reads the flag fresh.
def test_turning_off_prevents_next_automatic_execution():
    integration = _integration()
    brand_off = {"id": "brand-1", "cancellation_autopilot_enabled": False}
    result, mock_approve = _run_cancel_intent(integration, brand_off, order_id="1014")

    mock_approve.assert_not_called()
    assert "CANCEL QUEUED" in result["action_context"]


# 18. Tenant isolation remains intact - autopilot execution goes through
# the same tenant-scoped actions_service.approve_action() as human
# approval; a mismatched tenant_id can never execute another tenant's
# action (already proven generically by test_action_lifecycle_safety.py's
# wrong-tenant test for approve_action itself - this asserts the autopilot
# call site passes the real request's own tenant_id through unmodified,
# never a different/blank one).
def test_autopilot_call_passes_through_the_requests_own_tenant_id():
    integration = _integration()
    brand_on = {"id": "brand-1", "cancellation_autopilot_enabled": True}
    _, mock_approve = _run_cancel_intent(integration, brand_on, order_id="1013")
    _, kwargs = mock_approve.call_args
    assert kwargs["tenant_id"] == "tenant-1"
