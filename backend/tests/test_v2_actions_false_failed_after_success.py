"""
P0 fix regression test for v2_actions.py's /approve route: same false-FAILED-
after-Shopify-success bug as actions_service.py (see
test_action_execution_false_failed_after_success.py for the full root-cause
writeup), fixed the same way - the post-success status write/action_logs
insert are now isolated in their own try/except so a failure there can never
fall into the outer `except Exception as exec_error`, which used to set
execution_error and mark the action "failed" (retryable via /retry).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

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


def test_shopify_success_then_bookkeeping_failure_does_not_mark_action_failed():
    action = {"id": "action-1", "brand_id": OWN_BRAND, "status": "pending",
              "action_type": "refund", "order_id": "1001"}

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        if table == "brands":
            return [{"id": OWN_BRAND, "shopify_connected": True,
                      "shopify_domain": "x.myshopify.com", "shopify_access_token": "enc"}]
        return []

    def fake_update(table, match, data):
        if table == "actions" and match.get("status") == "eq.pending":
            return {"id": "action-1", "status": "approved"}  # atomic claim
        if table == "actions" and data.get("status") == "executed":
            raise Exception("simulated Supabase outage writing executed status")
        return {}

    with patch("src.api.routes.v2_actions.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_actions.supabase_update", side_effect=fake_update) as mock_update, \
         patch("src.api.routes.v2_actions.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.process_refund",
               return_value={"success": True, "message": "ok"}) as mock_refund, \
         patch("src.services.actions_service.actions_service._post_execution_notify", new=AsyncMock()):
        resp = client.post("/api/v2/actions/action-1/approve")

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True, (
        f"GAP: Shopify executed successfully but the caller was told it failed: {resp.json()}"
    )
    mock_refund.assert_called_once()

    failed_writes = [
        c for c in mock_update.call_args_list
        if len(c.args) >= 3 and c.args[2].get("status") == "failed"
    ]
    assert failed_writes == [], (
        f"GAP: a Shopify-success action was marked 'failed' after a downstream DB error, "
        f"which /retry could reset to 'pending' for a second real Shopify call: {failed_writes}"
    )


def test_normal_refund_approval_without_downstream_failure_still_succeeds():
    action = {"id": "action-1", "brand_id": OWN_BRAND, "status": "pending",
              "action_type": "refund", "order_id": "1001"}
    update_calls = []

    def fake_select(table, params=None):
        if table == "actions":
            return [action]
        if table == "brands":
            return [{"id": OWN_BRAND, "shopify_connected": True,
                      "shopify_domain": "x.myshopify.com", "shopify_access_token": "enc"}]
        return []

    def fake_update(table, match, data):
        update_calls.append((match, data))
        return {"id": "action-1", **data}

    with patch("src.api.routes.v2_actions.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_actions.supabase_update", side_effect=fake_update), \
         patch("src.api.routes.v2_actions.supabase_insert", return_value={}), \
         patch("src.services.shopify_service.decrypt_token", return_value="tok"), \
         patch("src.services.shopify_service.ShopifyClient.process_refund",
               return_value={"success": True, "message": "ok"}) as mock_refund, \
         patch("src.services.actions_service.actions_service._post_execution_notify", new=AsyncMock()):
        resp = client.post("/api/v2/actions/action-1/approve")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_refund.assert_called_once()
    executed_writes = [d for _, d in update_calls if d.get("status") == "executed"]
    assert len(executed_writes) == 1, (
        f"Normal successful approval did not persist the EXECUTED terminal status: {update_calls}"
    )
