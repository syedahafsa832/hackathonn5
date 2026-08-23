"""
brand_actions.py had zero authentication on any route - including
/api/brand-actions/approve/{id}, which executes a real Shopify refund/
cancel/address-change - despite being registered live in main.py at
/api/brand-actions/*. Confirmed reachable in production and confirmed no
other route provided a secure implementation of this table's approval flow
(v2_actions.py secures the parallel `actions` table, not `brand_actions`).

Fixed by requiring an authenticated agent/admin (require_agent_or_admin,
the same dependency v2_actions.py already uses) plus an explicit brand
ownership check on every route, and by making
multi_brand_actions.approve_action()/reject_action() atomically claim the
row (status=eq.pending in the WHERE clause) before mutating it - the same
double-approval/double-execution race class already fixed in
actions_service.py and v2_actions.py, which brand_actions.py's approve/
reject never had at all.

These tests cover, per the task brief:
  - unauthenticated approval attempt
  - authenticated wrong-tenant approval attempt
  - valid merchant approval (passes the ownership gate)
  - duplicate approval (second concurrent/retried approve does not execute
    a second Shopify action)
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.brand_actions import router as brand_actions_router  # noqa: E402
from src.api.middleware.auth_middleware import get_current_user, UserContext, AuthenticatedContext, UserRole  # noqa: E402
from src.services.multi_brand_actions import multi_brand_actions, ActionStatus  # noqa: E402

app = FastAPI()
app.include_router(brand_actions_router, prefix="/api")
client = TestClient(app)

ATTACKER_ORG = "org-attacker"
ATTACKER_OWN_BRAND = "brand-attacker-owned"
VICTIM_BRAND = "brand-victim"
ACTION_ID = "11111111-1111-1111-1111-111111111111"


def _attacker_admin_context() -> AuthenticatedContext:
    """Admin role, but only within their own org/brand."""
    return AuthenticatedContext(
        user=UserContext(
            user_id="user-attacker", supabase_auth_id="auth-attacker",
            organization_id=ATTACKER_ORG, email="attacker@example.com",
            role=UserRole.ADMIN, brands=[ATTACKER_OWN_BRAND],
        ),
        organization=None,
        brand_ids=[ATTACKER_OWN_BRAND],
    )


def teardown_function():
    app.dependency_overrides.clear()


def _fake_victim_action_select(table, params=None):
    if table == "brand_actions":
        return [{
            "id": ACTION_ID, "brand_id": VICTIM_BRAND, "status": "pending",
            "action_type": "cancel_order", "order_id": "1001",
            "customer_email": "victim-customer@example.com",
        }]
    return []


# ---------------------------------------------------------------------------
# 1. Unauthenticated approval attempt
# ---------------------------------------------------------------------------

def test_unauthenticated_approval_is_rejected():
    """No Authorization header at all -> 401, never reaches the DB/Shopify."""
    with patch("src.lib.supabase_client.supabase_select", side_effect=_fake_victim_action_select), \
         patch("src.services.multi_brand_actions.supabase_update") as mock_update:
        resp = client.post(f"/api/brand-actions/approve/{ACTION_ID}", json={"approved_by": "nobody"})

    assert resp.status_code == 401
    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Authenticated wrong-tenant approval attempt
# ---------------------------------------------------------------------------

def test_authenticated_wrong_tenant_approval_is_blocked():
    """A real authenticated admin, but of a DIFFERENT brand than the action -
    must not be able to approve (and thereby execute) another merchant's
    action. This is the exact bug that was live and unauthenticated before."""
    app.dependency_overrides[get_current_user] = _attacker_admin_context

    with patch("src.lib.supabase_client.supabase_select", side_effect=_fake_victim_action_select), \
         patch("src.services.multi_brand_actions.supabase_update") as mock_update:
        resp = client.post(f"/api/brand-actions/approve/{ACTION_ID}", json={"approved_by": "attacker"})

    assert resp.status_code == 403
    mock_update.assert_not_called()


def test_authenticated_wrong_tenant_reject_is_also_blocked():
    app.dependency_overrides[get_current_user] = _attacker_admin_context

    with patch("src.lib.supabase_client.supabase_select", side_effect=_fake_victim_action_select), \
         patch("src.services.multi_brand_actions.supabase_update") as mock_update:
        resp = client.post(f"/api/brand-actions/reject/{ACTION_ID}", json={"rejection_reason": "test"})

    assert resp.status_code == 403
    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Valid merchant approval passes the ownership gate
# ---------------------------------------------------------------------------

def test_valid_merchant_approval_passes_the_ownership_gate():
    """The legitimate case: an admin approving their OWN brand's action is
    not blocked by the ownership check (it may still fail/succeed later for
    unrelated reasons - the point here is only that 403 is not the reason)."""
    app.dependency_overrides[get_current_user] = _attacker_admin_context

    def fake_select(table, params=None):
        if table == "brand_actions":
            return [{
                "id": ACTION_ID, "brand_id": ATTACKER_OWN_BRAND, "status": "pending",
                "action_type": "cancel_order", "order_id": "1001",
                "customer_email": "customer@example.com", "extracted_data": {},
            }]
        return []

    with patch("src.lib.supabase_client.supabase_select", side_effect=fake_select), \
         patch("src.services.multi_brand_actions.supabase_select", side_effect=fake_select), \
         patch("src.services.multi_brand_actions.supabase_update", return_value={}), \
         patch("src.services.multi_brand_actions.brand_manager.get_brand", new=AsyncMock(return_value=None)):
        resp = client.post(f"/api/brand-actions/approve/{ACTION_ID}", json={"approved_by": "attacker"})

    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# 4. Duplicate approval does not execute a second Shopify action
# ---------------------------------------------------------------------------

class _FakeActionStore:
    """Emulates the `status=eq.pending` conditional UPDATE Postgres/PostgREST
    performs: an update only "matches" (returns a non-empty result) if the
    row's current status still satisfies the WHERE clause at call time."""

    def __init__(self):
        self.status = "pending"

    def select(self, table, params=None):
        if table == "brand_actions":
            return [{
                "id": ACTION_ID, "brand_id": ATTACKER_OWN_BRAND, "status": self.status,
                "action_type": "cancel_order", "order_id": "1001",
                "customer_email": "customer@example.com", "extracted_data": {},
            }]
        return []

    def update(self, table, match, data):
        if table != "brand_actions":
            return {}
        required_status = match.get("status")
        if required_status is not None:
            if required_status != f"eq.{self.status}":
                return {}  # WHERE id=... AND status=eq.X matched zero rows
        if "status" in data:
            self.status = data["status"]
        return {"id": ACTION_ID, **data}


def test_duplicate_approval_only_executes_once():
    """Two approve calls against the same pending action (double-click,
    retry) - only the first may claim and execute; the second must see the
    row already claimed and must not trigger a second Shopify mutation."""
    store = _FakeActionStore()
    cancel_order = AsyncMock(return_value={"success": True, "order_name": "#1001"})
    fake_shopify_client = type("FakeClient", (), {"cancel_order": cancel_order})()

    with patch("src.services.multi_brand_actions.supabase_select", side_effect=store.select), \
         patch("src.services.multi_brand_actions.supabase_update", side_effect=store.update), \
         patch("src.services.multi_brand_actions.brand_manager.get_brand", new=AsyncMock(return_value={"id": ATTACKER_OWN_BRAND})), \
         patch("src.services.multi_brand_actions.brand_manager.get_shopify_client", return_value=fake_shopify_client), \
         patch.object(multi_brand_actions, "_send_confirmation_email", new=AsyncMock()), \
         patch.object(multi_brand_actions, "_log_action", new=AsyncMock()):

        import asyncio
        first = asyncio.run(multi_brand_actions.approve_action(ACTION_ID, approved_by="agent-a"))
        second = asyncio.run(multi_brand_actions.approve_action(ACTION_ID, approved_by="agent-b"))

    assert first["success"] is True
    assert second["success"] is False
    assert "already" in second["error"].lower()
    # The core assertion: Shopify's cancel_order was only ever called once,
    # regardless of how many times approve_action was invoked.
    cancel_order.assert_called_once()
    assert store.status == ActionStatus.EXECUTED.value
