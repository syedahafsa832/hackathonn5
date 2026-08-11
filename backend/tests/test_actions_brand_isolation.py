"""
Cross-tenant IDOR in the actions queue - get/create/approve/reject/retry
(security audit finding B6, discovered while reviewing other routes for the
same class of bug found in v2_knowledge.py).

get_action, create_action, approve_action, reject_action, and retry_action
in v2_actions.py all contained the same broken check:

    if action["brand_id"] not in context.brand_ids:
        if not UserRole.is_admin(context.user.role):
            raise HTTPException(status_code=403, detail="Access denied")

Any admin-role user (of their OWN org) satisfied the inner `is_admin` check
and fell through without ever being rejected - regardless of whose brand the
action belonged to. approve_action executes a real Shopify refund/
cancellation/address-change, so this meant any admin-role user could approve
(and thereby financially execute) another tenant's staged action.

v2_tickets.py had already fixed this exact bug class with an unconditional
ownership check and a comment explaining why the bypass was wrong
(get_user_context() already scopes context.brand_ids to every brand in the
caller's own org, admin or not - so no additional bypass is ever needed).
The fix here applies the same pattern to v2_actions.py.

These tests focus on approve_action (the highest-impact one - real money
movement) via FastAPI's TestClient with get_current_user overridden, the
same technique used in test_knowledge_base_brand_isolation.py.
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

ATTACKER_ORG = "org-attacker"
ATTACKER_OWN_BRAND = "brand-attacker-owned"
VICTIM_BRAND = "brand-victim"


def _attacker_admin_context() -> AuthenticatedContext:
    """Admin role, but only within their own org/brand - the exact profile
    that exploited the bug (any org's admin, not just a platform superadmin)."""
    return AuthenticatedContext(
        user=UserContext(
            user_id="user-attacker", supabase_auth_id="auth-attacker",
            organization_id=ATTACKER_ORG, email="attacker@example.com",
            role=UserRole.ADMIN, brands=[ATTACKER_OWN_BRAND],
        ),
        organization=None,
        brand_ids=[ATTACKER_OWN_BRAND],
    )


def setup_function():
    app.dependency_overrides[get_current_user] = _attacker_admin_context


def teardown_function():
    app.dependency_overrides.clear()


def _fake_actions_select(table, params=None):
    if table == "actions":
        return [{
            "id": "action-1", "brand_id": VICTIM_BRAND, "status": "pending",
            "action_type": "refund", "customer_email": "victim-customer@example.com",
        }]
    return []


def test_approve_another_tenants_action_is_blocked():
    """The core exploit: an admin of one org must not be able to approve -
    and thereby execute a real Shopify refund for - another org's action."""
    with patch("src.api.routes.v2_actions.supabase_select", side_effect=_fake_actions_select), \
         patch("src.api.routes.v2_actions.supabase_update") as mock_update:
        resp = client.post("/api/v2/actions/action-1/approve")

    assert resp.status_code == 403
    # The exploit's whole point was that execution proceeded anyway - prove
    # it does not: no state-changing update was ever issued.
    mock_update.assert_not_called()


def test_reject_another_tenants_action_is_blocked():
    with patch("src.api.routes.v2_actions.supabase_select", side_effect=_fake_actions_select), \
         patch("src.api.routes.v2_actions.supabase_update") as mock_update:
        resp = client.post("/api/v2/actions/action-1/reject", json={"reason": "test"})

    assert resp.status_code == 403
    mock_update.assert_not_called()


def test_retry_another_tenants_action_is_blocked():
    with patch("src.api.routes.v2_actions.supabase_select", side_effect=_fake_actions_select), \
         patch("src.api.routes.v2_actions.supabase_update") as mock_update:
        resp = client.post("/api/v2/actions/action-1/retry")

    assert resp.status_code == 403
    mock_update.assert_not_called()


def test_get_another_tenants_action_is_blocked():
    with patch("src.api.routes.v2_actions.supabase_select", side_effect=_fake_actions_select):
        resp = client.get("/api/v2/actions/action-1")

    assert resp.status_code == 403


def test_create_action_for_another_tenants_brand_is_blocked():
    with patch("src.api.routes.v2_actions.supabase_select", side_effect=_fake_actions_select):
        resp = client.post("/api/v2/actions", json={
            "brand_id": VICTIM_BRAND, "action_type": "refund",
            "customer_email": "victim-customer@example.com",
        })

    assert resp.status_code == 403


def test_approve_own_brands_action_passes_the_ownership_gate():
    """Legitimate use is preserved: an admin approving their OWN brand's
    action is not blocked by the ownership check (it may still fail later
    for unrelated reasons, e.g. Shopify not connected in this test double -
    the point here is only that 403 "Access denied" is not the reason)."""
    def fake_select(table, params=None):
        if table == "actions":
            return [{"id": "action-1", "brand_id": ATTACKER_OWN_BRAND, "status": "pending"}]
        if table == "brands":
            return [{"id": ATTACKER_OWN_BRAND, "shopify_connected": False}]
        return []

    with patch("src.api.routes.v2_actions.supabase_select", side_effect=fake_select):
        resp = client.post("/api/v2/actions/action-1/approve")

    assert resp.status_code != 403
