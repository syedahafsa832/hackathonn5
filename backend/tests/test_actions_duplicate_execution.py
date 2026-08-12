"""
Duplicate-execution race in v2_actions.py's approve_action (reliability pass).

The route checked `action["status"] != "pending"` and then, separately,
unconditionally set status to "approved" before calling Shopify - a
check-then-act race. Two concurrent approve requests (double-click, retry)
could both read "pending" before either write landed, and both proceed to
call client.process_refund()/cancel_order() against the same order, issuing
a real duplicate refund or duplicate cancel attempt.

Fix: the status update is now conditioned on "status": "eq.pending" in the
same call (an atomic claim) - the same pattern already proven correct in
actions_service.py's approve_action(). supabase_update() returns {} (falsy)
when the WHERE clause matches nothing, so a second concurrent caller gets a
400 "already actioned" instead of proceeding to execute Shopify a second
time.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.v2_actions import router as v2_actions_router  # noqa: E402
from src.api.middleware.auth_middleware import get_current_user, UserContext, AuthenticatedContext, UserRole  # noqa: E402

app = FastAPI()
app.include_router(v2_actions_router, prefix="/api/v2")
client = TestClient(app)

OWN_BRAND = "brand-1"


def _context() -> AuthenticatedContext:
    return AuthenticatedContext(
        user=UserContext(
            user_id="user-1", supabase_auth_id="auth-1",
            organization_id="org-1", email="owner@example.com",
            role=UserRole.ADMIN, brands=[OWN_BRAND],
        ),
        organization=None,
        brand_ids=[OWN_BRAND],
    )


def setup_function():
    app.dependency_overrides[get_current_user] = _context


def teardown_function():
    app.dependency_overrides.clear()


def test_second_concurrent_approve_is_rejected_not_executed_twice():
    """Simulates the race directly: the action is "pending" when read, but
    the atomic claim (supabase_update with status=eq.pending in the WHERE
    clause) has already been won by another request, so it returns no rows."""
    def fake_select(table, params=None):
        if table == "actions":
            return [{"id": "action-1", "brand_id": OWN_BRAND, "status": "pending", "action_type": "refund"}]
        if table == "brands":
            return [{"id": OWN_BRAND, "shopify_connected": True, "shopify_domain": "x.myshopify.com", "shopify_access_token": "enc"}]
        return []

    with patch("src.api.routes.v2_actions.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_actions.supabase_update", return_value={}) as mock_update, \
         patch("src.services.shopify_service.ShopifyClient.process_refund") as mock_refund:
        resp = client.post("/api/v2/actions/action-1/approve")

    assert resp.status_code == 400
    assert "already actioned" in resp.json()["detail"].lower()
    # The whole point: Shopify must never be called once the claim is lost.
    mock_refund.assert_not_called()
    # Confirms the claim attempt used the atomic filter, not an unconditional update.
    match_arg = mock_update.call_args[0][1]
    assert match_arg.get("status") == "eq.pending"


def test_approve_still_executes_when_claim_succeeds():
    """Not a race: the claim succeeds (returns the row), so execution
    proceeds as before - this fix must not block normal approval."""
    def fake_select(table, params=None):
        if table == "actions":
            return [{"id": "action-1", "brand_id": OWN_BRAND, "status": "pending", "action_type": "refund", "order_id": "1001"}]
        if table == "brands":
            return [{"id": OWN_BRAND, "shopify_connected": True, "shopify_domain": "x.myshopify.com", "shopify_access_token": "enc"}]
        return []

    with patch("src.api.routes.v2_actions.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_actions.supabase_update", return_value={"id": "action-1", "status": "approved"}), \
         patch("src.api.routes.v2_actions.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.process_refund", return_value={"success": True, "message": "ok"}), \
         patch("src.services.actions_service.actions_service._post_execution_notify", return_value=None):
        resp = client.post("/api/v2/actions/action-1/approve")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
