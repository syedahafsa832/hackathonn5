"""
Partial refunds — human-entered amount, never AI-extracted.

Backlog decision (explicit): autonomous LLM-driven amount extraction from a
customer's message was deliberately NOT implemented — inferring a dollar
figure from "vague language" for a financial action is exactly the kind of
prompt-only inference this product's safety principles exist to avoid, and
there's no live-Shopify way to verify an LLM's guess is what the customer
actually meant. Instead: the human approver can type an explicit amount in
the dashboard at approval time. AI staging is completely unchanged.

This also fixes two real, concrete gaps found by tracing the full pipeline:
1. ShopifyClient.process_refund() had no check that a caller-provided amount
   doesn't exceed the order's actual refundable amount — Shopify's own API
   behavior here isn't guaranteed, so this is now enforced deterministically
   against live order state before any Shopify call is made.
2. Two separate approve-action code paths read a refund amount from two
   DIFFERENT places: actions_service.py (the one the real dashboard approve
   button actually calls) reads extracted_data.amount; v2_actions.py's own
   inline approve route read a different top-level "amount" column. Not
   currently exploitable (nothing in the live dashboard writes the top-level
   column), but a real latent inconsistency — v2_actions.py now prefers
   extracted_data.amount too, so both paths agree.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.shopify_service import ShopifyClient, ShopifyError  # noqa: E402
from src.services.actions_service import ActionsService  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _client():
    return ShopifyClient("test-shop.myshopify.com", "shpat_test")


def _order_response(**overrides):
    order = {
        "id": 123456789, "name": "#1001", "total_price": "100.00",
        "cancelled_at": None, "refunds": [], "fulfillment_status": None,
        "gateway": "manual",
    }
    order.update(overrides)
    return {"data": {"orders": [order]}}


# ── ShopifyClient.process_refund: amount validation ──────────────────────────

def test_full_refund_when_amount_not_specified():
    client = _client()
    order_resp = _order_response()
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 999, "transactions": [{"id": 2, "kind": "refund", "status": "success"}]}}}
    with patch.object(client, "_request", side_effect=[order_resp, txn_resp, refund_resp]):
        result = run(client.process_refund("1001"))
    assert result["success"] is True


def test_valid_partial_refund_within_refundable_amount_succeeds():
    client = _client()
    order_resp = _order_response(total_price="100.00")
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 999, "transactions": [{"id": 2, "kind": "refund", "status": "success"}]}}}
    captured = {}

    def fake_request(method, endpoint, data=None, params=None):
        if "refunds.json" in endpoint:
            captured["amount"] = data["refund"]["transactions"][0]["amount"]
            return refund_resp
        if "transactions.json" in endpoint:
            return txn_resp
        return order_resp

    with patch.object(client, "_request", side_effect=fake_request):
        result = run(client.process_refund("1001", amount=30.0))
    assert result["success"] is True
    assert captured["amount"] == "30.0" or captured["amount"] == "30.00" or float(captured["amount"]) == 30.0


def test_partial_refund_amount_exceeding_refundable_is_rejected():
    """The exact gap found by tracing the pipeline: no check previously
    existed for amount > refundable_amount."""
    client = _client()
    order_resp = _order_response(total_price="100.00")
    with patch.object(client, "_request", return_value=order_resp):
        with pytest.raises(ShopifyError) as exc_info:
            run(client.process_refund("1001", amount=150.0))
    assert "exceeds" in exc_info.value.message.lower()


def test_partial_refund_amount_exceeding_after_prior_partial_refund_is_rejected():
    """refundable_amount accounts for an existing partial refund — a second
    request for more than what's left must also be rejected."""
    client = _client()
    order_resp = _order_response(total_price="100.00", refunds=[{"transactions": [{"amount": "40.00"}]}])
    with patch.object(client, "_request", return_value=order_resp):
        with pytest.raises(ShopifyError) as exc_info:
            # 70 requested, only 60 left refundable
            run(client.process_refund("1001", amount=70.0))
    assert "exceeds" in exc_info.value.message.lower()


def test_partial_refund_amount_at_exact_refundable_boundary_succeeds():
    """Exactly the refundable amount (not exceeding it) must still work —
    the epsilon tolerance exists for float rounding, not to reject valid
    full amounts."""
    client = _client()
    order_resp = _order_response(total_price="100.00")
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 999, "transactions": [{"id": 2, "kind": "refund", "status": "success"}]}}}
    with patch.object(client, "_request", side_effect=[order_resp, txn_resp, refund_resp]):
        result = run(client.process_refund("1001", amount=100.0))
    assert result["success"] is True


def test_zero_or_negative_amount_already_rejected_by_existing_check():
    client = _client()
    order_resp = _order_response(total_price="100.00")
    with patch.object(client, "_request", return_value=order_resp):
        with pytest.raises(ShopifyError):
            run(client.process_refund("1001", amount=0))


# ── actions_service.approve_action: human-entered override_amount ───────────

def _make_service():
    return ActionsService()


def test_override_amount_less_than_or_equal_zero_rejected_before_any_shopify_call():
    svc = _make_service()
    with patch.object(svc, "get_action", new=AsyncMock()) as mock_get_action:
        result = run(svc.approve_action(tenant_id="t1", action_id="a1", override_amount=-5.0))
    assert result["success"] is False
    assert result["error_code"] == "invalid_amount"
    mock_get_action.assert_not_called()  # rejected before even touching the action/Shopify


def _approved_action(amount_in_extracted_data=20.0):
    return {
        "id": "a1", "action_type": "refund", "order_id": "1001",
        "status": "pending", "ticket_id": None, "brand_id": "brand-1",
        "extracted_data": {"amount": amount_in_extracted_data} if amount_in_extracted_data is not None else {},
    }


def _run_approve(action, override_amount=None):
    captured = {}

    async def fake_refund(order_id, amount=None, reason=None, **kwargs):
        captured["amount"] = amount
        return {"success": True, "refund_id": 999, "amount": amount, "message": "Refunded"}

    fake_client = type("FakeClient", (), {"process_refund": staticmethod(fake_refund)})()
    svc = _make_service()

    with patch.object(svc, "get_action", new=AsyncMock(return_value=action)), \
         patch("src.services.actions_service.supabase_update", return_value={"id": "a1"}), \
         patch("src.services.actions_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)), \
         patch.object(svc, "_log_event", new=AsyncMock()), \
         patch.object(svc, "_post_execution_notify", new=AsyncMock()), \
         patch.object(svc, "_record_financial_audit"):
        result = run(svc.approve_action(tenant_id="t1", action_id="a1", override_amount=override_amount))

    return result, captured


def test_override_amount_takes_precedence_over_extracted_data_amount():
    result, captured = _run_approve(_approved_action(amount_in_extracted_data=20.0), override_amount=75.0)
    assert result["success"] is True
    assert captured["amount"] == 75.0  # not the staged extracted_data.amount (20.0)


def test_no_override_amount_falls_back_to_extracted_data_amount_unchanged():
    """No regression: when the approver doesn't type an amount, behavior is
    byte-for-byte what it was before this change."""
    result, captured = _run_approve(_approved_action(amount_in_extracted_data=20.0), override_amount=None)
    assert result["success"] is True
    assert captured["amount"] == 20.0


def test_no_override_and_no_extracted_amount_means_full_refund_default():
    """Neither set — process_refund's own amount=None default (full refund)
    is what gets requested, exactly as before this change existed."""
    result, captured = _run_approve(_approved_action(amount_in_extracted_data=None), override_amount=None)
    assert result["success"] is True
    assert captured["amount"] is None


# ── edit-tracking: was the AI's proposal changed before approval? ───────────
# (migrations/046_action_edit_tracking.sql — not applied to production by
# that migration; this write is deliberately best-effort/non-blocking, see
# actions_service._record_edit_tracking's docstring for why.)

def _run_approve_capturing_updates(action, override_amount=None):
    """Like _run_approve, but records every supabase_update call (not just
    the Shopify-side captured amount) so edit-tracking's own update can be
    asserted on independently of the atomic-claim/execution updates."""
    update_calls = []

    def fake_update(table, match, data):
        update_calls.append((table, match, data))
        return {"id": "a1"}

    async def fake_refund(order_id, amount=None, reason=None, **kwargs):
        return {"success": True, "refund_id": 999, "amount": amount, "message": "Refunded"}

    fake_client = type("FakeClient", (), {"process_refund": staticmethod(fake_refund)})()
    svc = _make_service()

    with patch.object(svc, "get_action", new=AsyncMock(return_value=action)), \
         patch("src.services.actions_service.supabase_update", side_effect=fake_update), \
         patch("src.services.actions_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)), \
         patch.object(svc, "_log_event", new=AsyncMock()), \
         patch.object(svc, "_post_execution_notify", new=AsyncMock()), \
         patch.object(svc, "_record_financial_audit"):
        result = run(svc.approve_action(tenant_id="t1", action_id="a1", override_amount=override_amount))

    return result, update_calls


def test_overridden_refund_amount_is_recorded_as_edited():
    result, update_calls = _run_approve_capturing_updates(
        _approved_action(amount_in_extracted_data=20.0), override_amount=75.0
    )
    assert result["success"] is True
    tracking_calls = [c for c in update_calls if "was_edited" in c[2]]
    assert len(tracking_calls) == 1
    _, _, data = tracking_calls[0]
    assert data["was_edited"] is True
    assert data["approved_extracted_data"]["amount"] == 75.0


def test_unedited_refund_amount_is_not_recorded_as_edited():
    """No override at all — nothing to distinguish, so no tracking update
    should be issued (was_edited's column default of false already covers
    this row correctly without a write)."""
    result, update_calls = _run_approve_capturing_updates(
        _approved_action(amount_in_extracted_data=20.0), override_amount=None
    )
    assert result["success"] is True
    tracking_calls = [c for c in update_calls if "was_edited" in c[2]]
    assert tracking_calls == []


def test_override_matching_the_original_proposal_is_not_recorded_as_edited():
    """The approver typed the exact same amount the AI had already proposed
    — that's not a correction, so it must not be flagged as edited."""
    result, update_calls = _run_approve_capturing_updates(
        _approved_action(amount_in_extracted_data=20.0), override_amount=20.0
    )
    assert result["success"] is True
    tracking_calls = [c for c in update_calls if "was_edited" in c[2]]
    assert tracking_calls == []


def test_edit_tracking_write_failure_never_breaks_approval():
    """If migration 046 isn't applied yet in some environment, the
    supabase_update for was_edited/approved_extracted_data will fail (unknown
    column) — that must never surface as an approval failure, only be
    swallowed and logged, since it's enrichment, not part of the state
    machine."""
    action = _approved_action(amount_in_extracted_data=20.0)

    def flaky_update(table, match, data):
        if "was_edited" in data:
            raise Exception('column "was_edited" does not exist')
        return {"id": "a1"}

    async def fake_refund(order_id, amount=None, reason=None, **kwargs):
        return {"success": True, "refund_id": 999, "amount": amount, "message": "Refunded"}

    fake_client = type("FakeClient", (), {"process_refund": staticmethod(fake_refund)})()
    svc = _make_service()

    with patch.object(svc, "get_action", new=AsyncMock(return_value=action)), \
         patch("src.services.actions_service.supabase_update", side_effect=flaky_update), \
         patch("src.services.actions_service.shopify_service.get_client_for_tenant", new=AsyncMock(return_value=fake_client)), \
         patch.object(svc, "_log_event", new=AsyncMock()), \
         patch.object(svc, "_post_execution_notify", new=AsyncMock()), \
         patch.object(svc, "_record_financial_audit"):
        result = run(svc.approve_action(tenant_id="t1", action_id="a1", override_amount=75.0))

    assert result["success"] is True


# ── v2_actions.py: the SECOND, also-live approval surface ───────────────────
# (Dashboard.jsx and TicketDetail.jsx both render ActionCard.jsx, which posts
# to this route — a genuinely separate, independently-reachable path from
# Actions.jsx's dedicated Action Queue page, which uses saas_actions.py
# instead. Both had to be fixed/extended for partial refunds to be safe
# regardless of which part of the dashboard the merchant uses.)

def test_v2_actions_approve_amount_precedence_request_over_extracted_over_legacy_column():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes.v2_actions import router as v2_actions_router
    from src.api.middleware.auth_middleware import get_current_user, UserContext, AuthenticatedContext, UserRole

    app = FastAPI()
    app.include_router(v2_actions_router, prefix="/api/v2")
    test_client = TestClient(app)
    brand_id = "brand-1"

    def own_context():
        return AuthenticatedContext(
            user=UserContext(user_id="user-1", supabase_auth_id="auth-1",
                              organization_id="org-1", email="owner@example.com",
                              role=UserRole.ADMIN, brands=[brand_id]),
            organization=None, brand_ids=[brand_id],
        )

    def fake_select(table, params=None):
        if table == "actions":
            return [{
                "id": "action-1", "brand_id": brand_id, "status": "pending",
                "action_type": "refund", "order_id": "1001",
                "extracted_data": {"amount": 20.0},  # would be used if no request amount given
                "amount": 5.0,  # legacy top-level column — lowest precedence
            }]
        if table == "brands":
            return [{"id": brand_id, "shopify_connected": True, "shopify_domain": "x.myshopify.com", "shopify_access_token": "enc"}]
        return []

    captured = {}

    async def fake_process_refund(self, order_id, amount=None, reason=None, restock=True, notify_customer=False, **kwargs):
        captured["amount"] = amount
        return {"success": True, "refund_id": 999, "message": "ok"}

    app.dependency_overrides[get_current_user] = own_context
    try:
        with patch("src.api.routes.v2_actions.supabase_select", side_effect=fake_select), \
             patch("src.api.routes.v2_actions.supabase_update", return_value={"id": "action-1"}), \
             patch("src.api.routes.v2_actions.supabase_insert", return_value={}), \
             patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
             patch("src.services.shopify_service.ShopifyClient.process_refund", new=fake_process_refund), \
             patch("src.services.actions_service.actions_service._post_execution_notify", new=AsyncMock()):
            resp = test_client.post("/api/v2/actions/action-1/approve", json={"amount": 75.0})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert captured["amount"] == 75.0  # request body wins over both extracted_data (20.0) and legacy column (5.0)


def test_v2_actions_approve_rejects_non_positive_amount_at_the_api_layer():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes.v2_actions import router as v2_actions_router
    from src.api.middleware.auth_middleware import get_current_user, UserContext, AuthenticatedContext, UserRole

    app = FastAPI()
    app.include_router(v2_actions_router, prefix="/api/v2")
    test_client = TestClient(app)

    def own_context():
        return AuthenticatedContext(
            user=UserContext(user_id="user-1", supabase_auth_id="auth-1",
                              organization_id="org-1", email="owner@example.com",
                              role=UserRole.ADMIN, brands=["brand-1"]),
            organization=None, brand_ids=["brand-1"],
        )

    app.dependency_overrides[get_current_user] = own_context
    try:
        resp = test_client.post("/api/v2/actions/action-1/approve", json={"amount": -10})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422  # rejected by Pydantic before any DB/Shopify call
