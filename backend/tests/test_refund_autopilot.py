"""
tResolv — Phase 5: Refund Autopilot Execution.

A separate, independently-gated automation category from Cancellation
Autopilot (its own brands.refund_autopilot_enabled flag, its own
readiness computation, its own execution hook) - deliberately NOT a
copy-paste of Cancellation Autopilot, because refunds carry their own
financial safety gates:

- Autopilot only ever executes a full, whole-order refund for a
  Shopify-computed amount (approve_action called with NO override_amount,
  so process_refund() falls through to its own live
  refundable_amount = order_total - already_refunded calculation) - never
  a partial amount proposed by the model or stated by the customer.
- A specific single-item partial match, or any dollar figure mentioned in
  the customer's own message, is treated as inherently ambiguous and
  always falls through to the existing human-review Copilot path.

Two surfaces are covered, mirroring test_cancellation_autopilot.py:

A. POST /{brand_id}/automation/refund/{enable,disable} (v2_brands.py)
B. ReturnActionsIntegration._maybe_autopilot_refund / handle_return_intent's
   ELIGIBLE-refund branch (return_actions_integration.py)

Numbered comments map back to the task's 19 named test scenarios.
"""
import os
import sys
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services.return_actions_integration import ReturnActionsIntegration  # noqa: E402
from src.services.intent_detector import IntentResult  # noqa: E402


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
    "shopify_connected": True, "refund_autopilot_enabled": False,
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


def _ready_refund_actions():
    # 5 executed, 0 failed -> ready_for_review (matches _AUTOPILOT_MIN_SAMPLE=5)
    return [{"id": f"a{i}", "action_type": "refund", "status": "executed"} for i in range(5)]


def _almost_there_refund_actions(n=18):
    # Enough sample, but one real Shopify execution failure -> almost_there.
    rows = [{"id": f"a{i}", "action_type": "refund", "status": "executed"} for i in range(n - 1)]
    rows.append({"id": "a-fail", "action_type": "refund", "status": "failed", "error_message": "gateway declined"})
    return rows


# 1. Refund readiness calculation uses real outcomes.
def test_refund_readiness_uses_real_outcomes():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return _almost_there_refund_actions(18)
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    assert resp.status_code == 200
    refund = resp.json()["category_readiness"]["refund"]
    assert refund["total_requests"] == 18
    assert refund["successful"] == 17
    assert refund["failed_executions"] == 1
    assert refund["status"] == "almost_there"
    assert refund["mode"] == "copilot"
    assert refund["enabled"] is False


# 2. Insufficient refund history cannot enable Autopilot.
def test_enable_blocked_when_readiness_insufficient():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return [{"id": "a1", "action_type": "refund", "status": "executed"}]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update:
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/refund/enable"))

    assert resp.status_code == 400
    mock_update.assert_not_called()


# 3. Authorized merchant can enable when ready.
def test_enable_succeeds_when_readiness_sufficient():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return _ready_refund_actions()
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update", return_value={}) as mock_update, \
         patch("src.services.plan_service.check_limit", return_value={"allowed": True}):
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/refund/enable"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["enabled"] is True
    assert body["readiness"]["status"] == "ready_for_review"
    call_args = mock_update.call_args
    assert call_args[0][0] == "brands"
    assert call_args[0][2]["refund_autopilot_enabled"] is True


# 4. Wrong tenant cannot enable.
def test_enable_blocked_for_wrong_tenant():
    def fake_select(table, params=None):
        if table == "brands":
            if params and params.get("tenant_id") == "eq.tenant-attacker":
                return []
            return [BRAND]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update:
        resp = _with_tenant(
            lambda: client.post("/api/v2/brands/brand-1/automation/refund/enable"),
            tenant_id="tenant-attacker",
        )

    assert resp.status_code == 404
    mock_update.assert_not_called()


# 5. Unauthenticated user cannot enable.
def test_enable_requires_authentication():
    resp = client.post("/api/v2/brands/brand-1/automation/refund/enable")
    assert resp.status_code == 401


def test_enable_blocked_without_shopify_connection():
    brand = {**BRAND, "shopify_connected": False}

    def fake_select(table, params=None):
        if table == "brands":
            return [brand]
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update:
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/refund/enable"))

    assert resp.status_code == 400
    assert "Shopify" in resp.json()["detail"]
    mock_update.assert_not_called()


def test_enable_blocked_when_not_entitled():
    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            return _ready_refund_actions()
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update") as mock_update, \
         patch("src.services.plan_service.check_limit", return_value={"allowed": False, "used": 50, "limit": 50}):
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/refund/enable"))

    assert resp.status_code == 402
    mock_update.assert_not_called()


# 19 (endpoint half). Turning Refund Autopilot off requires only
# auth+ownership - no readiness gate, always allowed, always immediate.
def test_disable_always_allowed_regardless_of_readiness():
    brand_on = {**BRAND, "refund_autopilot_enabled": True}

    def fake_select(table, params=None):
        if table == "brands":
            return [brand_on]
        if table == "actions":
            return [{"id": "a1", "action_type": "refund", "status": "executed"}]  # not ready - irrelevant to disable
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_update", return_value={}) as mock_update:
        resp = _with_tenant(lambda: client.post("/api/v2/brands/brand-1/automation/refund/disable"))

    assert resp.status_code == 200
    assert resp.json() == {"success": True, "enabled": False}
    call_args = mock_update.call_args
    assert call_args[0][2]["refund_autopilot_enabled"] is False


# 17 (endpoint half). Tenant isolation.
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
            lambda: client.post("/api/v2/brands/brand-1/automation/refund/disable"),
            tenant_id="tenant-attacker",
        )
    assert resp.status_code == 404
    mock_update.assert_not_called()


# Cancellation and refund readiness never contaminate each other, and
# Cancellation Autopilot's own enable/disable endpoints are unaffected by
# this task's changes (15, endpoint half).
def test_cancellation_and_refund_readiness_are_independent():
    cancel_actions = [{"id": f"c{i}", "action_type": "cancel_order", "status": "executed"} for i in range(3)]
    refund_actions = [{"id": f"r{i}", "action_type": "refund", "status": "executed"} for i in range(7)]

    def fake_select(table, params=None):
        if table == "brands":
            return [BRAND]
        if table == "actions":
            action_filter = (params or {}).get("action_type", "")
            if "cancel_order" in action_filter:
                return cancel_actions
            if "refund" in action_filter:
                return refund_actions
            return []
        return []

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select):
        resp = _with_tenant(lambda: client.get("/api/v2/brands/brand-1/analytics"))

    body = resp.json()
    assert body["category_readiness"]["cancellation"]["total_requests"] == 3
    assert body["category_readiness"]["refund"]["total_requests"] == 7


# ══════════════════════════════════════════════════════════════════════════
# B. Autopilot execution hook (return_actions_integration.py)
# ══════════════════════════════════════════════════════════════════════════

def _integration():
    return ReturnActionsIntegration()


def _eligibility(order_id="2001", items=None):
    return {
        "eligible": True,
        "order": {"fulfillment_status": "fulfilled", "id": order_id},
        "items": items if items is not None else [{"title": "T-Shirt"}],
        "order_total": 42.0,
        "reason": None,
    }


def _run_refund_intent(
    integration, brand_row, autopilot_outcome=None, order_id="2001",
    query=None, items=None, find_active=None,
):
    async def _fake_find_active(tenant_id, oid, action_type):
        return find_active

    staged_result = {
        "success": True, "action_id": "action-1", "action_type": "refund",
        "status": "pending", "risk_level": "low",
    }
    mock_approve = AsyncMock(return_value=autopilot_outcome or {
        "success": True, "execution_result": {"amount": 42.0}, "message": "refund completed",
    })
    intent_result = IntentResult(action_type="refund", order_id=order_id, raw_address=None, confidence=0.9)

    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(
        return_value=_eligibility(order_id=order_id, items=items)
    )), patch.object(integration, "_find_active_action", new=_fake_find_active), \
         patch("src.services.return_actions_integration.supabase_select", side_effect=lambda t, p=None: (
             [brand_row] if t == "brands" else []
         )), \
         patch("src.services.actions_service.actions_service.create_action", new=AsyncMock(return_value=staged_result)), \
         patch("src.services.actions_service.actions_service.approve_action", new=mock_approve):
        result = run(integration.handle_return_intent(
            query=query or f"Please refund my order #{order_id}",
            customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={},
            tenant_id="tenant-1",
            brand_id="brand-1",
            ticket_id="ticket-1",
            intent_result=intent_result,
        ))
    return result, mock_approve


# 6. Disabled Refund Autopilot never executes automatically.
def test_disabled_refund_autopilot_never_auto_executes():
    integration = _integration()
    brand_off = {"id": "brand-1", "refund_autopilot_enabled": False}
    result, mock_approve = _run_refund_intent(integration, brand_off)

    mock_approve.assert_not_called()
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]
    assert "processed" not in result["action_context"].lower() or "Done!" not in result["action_context"]


# 7 / 18. Eligible refund executes exactly once, and produces a proper
# action/audit record via actions_service.approve_action (the same
# function human approval calls - no second execution path).
def test_enabled_refund_autopilot_executes_eligible_refund_exactly_once():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    result, mock_approve = _run_refund_intent(
        integration, brand_on,
        autopilot_outcome={"success": True, "execution_result": {"amount": 42.0}, "message": "Successfully refunded $42.00"},
        order_id="2001",
    )

    mock_approve.assert_called_once()
    _, kwargs = mock_approve.call_args
    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["action_id"] == "action-1"
    assert kwargs["approved_by"] == "autopilot"
    assert kwargs["idempotency_key"] == "autopilot-action-1"
    assert "override_amount" not in kwargs  # never a model/customer-supplied amount
    ctx = result["action_context"]
    assert "REFUND COMPLETED AUTOMATICALLY" in ctx
    assert "$42.00" in ctx
    assert "processed" in ctx


# 8. Duplicate refund cannot execute twice - the existing duplicate-action
# guard short-circuits a second request for the same order before
# eligibility/autopilot are ever reached.
def test_duplicate_refund_request_short_circuits_before_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    existing = {"id": "action-1", "status": "executed", "action_type": "refund", "order_id": "2001"}

    result, mock_approve = _run_refund_intent(integration, brand_on, find_active=existing)

    mock_approve.assert_not_called()
    assert result.get("staged") is None


# 9. Already-refunded order escalates - Shopify's own live refundable-
# amount check fails at execution time; Autopilot must never claim success.
def test_already_refunded_order_escalates():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    result, mock_approve = _run_refund_intent(
        integration, brand_on,
        autopilot_outcome={"success": False, "error": "Order has already been fully refunded."},
        order_id="2001",
    )

    mock_approve.assert_called_once()
    ctx = result["action_context"]
    assert "REFUND AUTOPILOT FAILED" in ctx
    assert "already been fully refunded" in ctx
    assert "processed" not in ctx.lower()


# 10. Invalid refund amount escalates - Shopify's own amount-vs-refundable
# validation rejects it; never trusted as a success.
def test_invalid_refund_amount_escalates():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    result, mock_approve = _run_refund_intent(
        integration, brand_on,
        autopilot_outcome={"success": False, "error": "Requested refund amount ($60.00) exceeds the refundable amount ($42.00) for this order."},
        order_id="2001",
    )

    mock_approve.assert_called_once()
    ctx = result["action_context"]
    assert "ESCALATED TO HUMAN REVIEW" in ctx
    assert "Done!" not in ctx


# 11. Ambiguous refund amount escalates - a customer stating their own
# dollar figure is never trusted; Autopilot is never even attempted.
def test_ambiguous_customer_stated_amount_never_reaches_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    result, mock_approve = _run_refund_intent(
        integration, brand_on, order_id="2001",
        query="Can you refund me $30 for the damaged item?",
    )

    mock_approve.assert_not_called()
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]


def test_ambiguous_amount_in_words_never_reaches_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    result, mock_approve = _run_refund_intent(
        integration, brand_on, order_id="2001",
        query="I want 30 dollars back for order #2001",
    )
    mock_approve.assert_not_called()


# Specific single-item partial match is equally ambiguous - this
# integration has no deterministic partial-item refund amount.
def test_specific_item_partial_match_never_reaches_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    items = [{"title": "Blue Hoodie"}, {"title": "Red Hat"}]
    result, mock_approve = _run_refund_intent(
        integration, brand_on, order_id="2001", items=items,
        query="Please refund just the blue hoodie from order #2001",
    )
    mock_approve.assert_not_called()
    assert "PARTIAL" in result["action_context"]


# 12. Policy restriction escalates - a fulfilled order that
# check_return_eligibility itself flags as ineligible (e.g. a merchant
# policy restriction) never reaches the ELIGIBLE/autopilot branch at all.
def test_policy_restriction_escalates_never_reaches_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}

    async def _no_active(tenant_id, oid, action_type):
        return None

    intent_result = IntentResult(action_type="refund", order_id="2001", raw_address=None, confidence=0.9)
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
        "eligible": False, "order": {"fulfillment_status": "fulfilled", "id": "2001"},
        "items": [], "order_total": 42.0, "reason": "Store policy requires human approval for this refund.",
        "staging_required": True,
    })), patch.object(integration, "_find_active_action", new=_no_active), \
         patch("src.services.return_actions_integration.supabase_select", side_effect=lambda t, p=None: [brand_on] if t == "brands" else []), \
         patch("src.services.actions_service.actions_service.create_action", new=AsyncMock(return_value={
             "success": True, "action_id": "action-1", "action_type": "refund", "status": "pending", "risk_level": "low",
         })), \
         patch("src.services.actions_service.actions_service.approve_action", new=AsyncMock()) as mock_approve:
        result = run(integration.handle_return_intent(
            query="refund order #2001", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
            intent_result=intent_result,
        ))

    mock_approve.assert_not_called()
    assert "MANUAL REVIEW" in result["action_context"]


# 13 / 14. Shopify failure never produces success messaging, and creates
# the appropriate truthful escalation - no fabricated timing promise.
def test_shopify_failure_escalates_without_claiming_success():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    result, mock_approve = _run_refund_intent(
        integration, brand_on,
        autopilot_outcome={"success": False, "error": "Shopify reported the refund transaction did not succeed."},
        order_id="2001",
    )

    ctx = result["action_context"]
    assert "processed" not in ctx.lower() or "Done!" not in ctx
    assert "I couldn't complete that refund automatically" in ctx
    assert "sent it to our team for review" in ctx
    assert "within 2 hours" not in ctx
    assert "shortly" not in ctx.lower()


# 15. Cancellation Autopilot remains unaffected - the refund hook is a
# structurally separate call site from cancellation's own hook.
def test_cancellation_autopilot_call_site_is_structurally_separate():
    import inspect
    from src.services import return_actions_integration as rai_module
    source = inspect.getsource(rai_module.ReturnActionsIntegration)
    assert source.count("_maybe_autopilot_cancel(") == 2  # def + one call site
    assert source.count("_maybe_autopilot_refund(") == 2  # def + one call site


# 16. Refunds remain human-approved when Autopilot is OFF - byte-for-byte
# unchanged Copilot behavior (column absent, same as pre-migration state).
def test_copilot_behavior_unchanged_without_refund_autopilot_column():
    integration = _integration()
    brand_pre_migration = {"id": "brand-1"}  # no refund_autopilot_enabled key at all
    result, mock_approve = _run_refund_intent(integration, brand_pre_migration, order_id="2001")

    mock_approve.assert_not_called()
    assert result["action_context"] == (
        "**ACTION STAGED FOR APPROVAL**: Your refund request has been submitted for review. "
        "Tell the customer: 'I've prepared your request for my team to review. "
        "You'll get a confirmation once they approve it.'"
    )


# 17 (execution half). Tenant isolation - the autopilot call passes
# through the request's own real tenant_id unmodified.
def test_autopilot_call_passes_through_the_requests_own_tenant_id():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True}
    _, mock_approve = _run_refund_intent(integration, brand_on, order_id="2001")
    _, kwargs = mock_approve.call_args
    assert kwargs["tenant_id"] == "tenant-1"


# 19 (execution half). Turning Refund Autopilot off prevents the next
# automatic execution - the flag is read fresh on every request.
def test_turning_off_prevents_next_automatic_execution():
    integration = _integration()
    brand_off = {"id": "brand-1", "refund_autopilot_enabled": False}
    result, mock_approve = _run_refund_intent(integration, brand_off, order_id="2002")

    mock_approve.assert_not_called()
    assert "ACTION STAGED FOR APPROVAL" in result["action_context"]


# Cancel-order (unfulfilled) staging must never be treated as a refund
# autopilot candidate, even with Refund Autopilot enabled - that's
# Cancellation Autopilot's own, separate hook.
def test_unfulfilled_cancel_staging_never_triggers_refund_autopilot():
    integration = _integration()
    brand_on = {"id": "brand-1", "refund_autopilot_enabled": True, "cancellation_autopilot_enabled": False}

    async def _no_active(tenant_id, oid, action_type):
        return None

    intent_result = IntentResult(action_type="cancel", order_id="2003", raw_address=None, confidence=0.9)
    with patch.object(integration.actions, "check_return_eligibility", new=AsyncMock(return_value={
        "eligible": False, "order": {"fulfillment_status": "unfulfilled", "id": "2003"},
        "items": [], "order_total": 20.0, "reason": None,
    })), patch.object(integration.actions, "get_custom_policy_text", new=AsyncMock(return_value="")), \
         patch.object(integration, "_find_active_action", new=_no_active), \
         patch("src.services.return_actions_integration.supabase_select", side_effect=lambda t, p=None: [brand_on] if t == "brands" else []), \
         patch("src.services.actions_service.actions_service.create_action", new=AsyncMock(return_value={
             "success": True, "action_id": "action-3", "action_type": "cancel_order", "status": "pending", "risk_level": "low",
         })), \
         patch("src.services.actions_service.actions_service.approve_action", new=AsyncMock()) as mock_approve:
        result = run(integration.handle_return_intent(
            query="cancel order #2003", customer_info={"email": "c@example.com", "name": "Casey"},
            existing_tool_results={}, tenant_id="tenant-1", brand_id="brand-1", ticket_id="ticket-1",
            intent_result=intent_result,
        ))

    mock_approve.assert_not_called()  # refund_autopilot_enabled=True must not auto-cancel
    assert "CANCEL QUEUED" in result["action_context"]
