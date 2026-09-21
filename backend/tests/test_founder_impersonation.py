"""
Founder/Admin Account Access (Impersonation) Tests
===================================================
Covers the smallest-viable "log in as customer" workflow added for the
tResolv founder: platform_admin.py's POST /admin/tenants/{id}/impersonate
mints a short-lived, non-refreshable token scoped to another tenant, and
tenant_auth.py's get_current_tenant verifies it through the exact same
trusted path legacy tenant JWTs already use.

What matters most here isn't the happy path alone — it's that:
- only a confirmed super-admin can mint one (item: preserve tenant isolation),
- it can only ever be minted for a real, active tenant,
- the resulting token resolves to the TARGET tenant's own id/email (not the
  admin's), so every existing tenant_id-scoped route stays isolated exactly
  as it is for a normal login,
- it can't be used to reach another admin-only route (no privilege chaining),
- no customer password is read, generated, or logged anywhere in the flow.
"""
import os
import sys
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.platform_admin import router as platform_admin_router  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services.auth_service import auth_service as real_auth_service  # noqa: E402

app = FastAPI()
app.include_router(platform_admin_router, prefix="/api/v2")


@app.get("/api/v2/_whoami")
async def _whoami(tenant: TenantContext = Depends(get_current_tenant)):
    """Minimal stand-in for any ordinary tenant-scoped route (tickets,
    actions, brands, ...) — every one of them resolves tenant identity
    through this same dependency, so proving it here proves it for all."""
    return {"tenant_id": tenant.tenant_id, "email": tenant.email}


client = TestClient(app)

ADMIN_TENANT_ID = "56dafdeb-bd22-4582-9f3a-3616009a43e4"
ADMIN_EMAIL = "syedahafsa1983@gmail.com"
NON_ADMIN_TENANT_ID = "99999999-9999-9999-9999-999999999999"
NON_ADMIN_EMAIL = "random.customer@example.com"
TARGET_TENANT_ID = "11111111-1111-1111-1111-111111111111"
TARGET_EMAIL = "customer@shop.example"


def _fake_select_active_target(table, params=None):
    if table == "tenants":
        return [{"id": TARGET_TENANT_ID, "email": TARGET_EMAIL,
                  "company_name": "Shop Example", "is_active": True}]
    return []


def _fake_select_inactive_target(table, params=None):
    if table == "tenants":
        return [{"id": TARGET_TENANT_ID, "email": TARGET_EMAIL,
                  "company_name": "Shop Example", "is_active": False}]
    return []


def _fake_select_missing_target(table, params=None):
    return []


def _admin_token():
    return real_auth_service.create_access_token(ADMIN_TENANT_ID, ADMIN_EMAIL)


def _non_admin_token():
    return real_auth_service.create_access_token(NON_ADMIN_TENANT_ID, NON_ADMIN_EMAIL)


def test_non_admin_cannot_mint_impersonation_token():
    with patch("src.api.routes.platform_admin.is_super_admin",
               side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL):
        resp = client.post(
            f"/api/v2/admin/tenants/{TARGET_TENANT_ID}/impersonate",
            headers={"Authorization": f"Bearer {_non_admin_token()}"},
        )
    assert resp.status_code == 403
    assert "access_token" not in resp.json()


def test_unauthenticated_request_rejected():
    resp = client.post(f"/api/v2/admin/tenants/{TARGET_TENANT_ID}/impersonate")
    assert resp.status_code == 401


def test_admin_cannot_impersonate_nonexistent_tenant():
    with patch("src.api.routes.platform_admin.supabase_select", side_effect=_fake_select_missing_target), \
         patch("src.api.routes.platform_admin.is_super_admin",
               side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL):
        resp = client.post(
            f"/api/v2/admin/tenants/{TARGET_TENANT_ID}/impersonate",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
    assert resp.status_code == 404


def test_admin_cannot_impersonate_disabled_tenant():
    with patch("src.api.routes.platform_admin.supabase_select", side_effect=_fake_select_inactive_target), \
         patch("src.api.routes.platform_admin.is_super_admin",
               side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL):
        resp = client.post(
            f"/api/v2/admin/tenants/{TARGET_TENANT_ID}/impersonate",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
    assert resp.status_code == 403


def test_admin_mints_impersonation_token_and_it_resolves_to_target_tenant():
    """The full loop: admin mints a token for TARGET_TENANT_ID, then that
    token (not the admin's own) is presented to an ordinary protected route
    — it must resolve to the target's own tenant_id/email, proving no cross-
    tenant bleed and no dependency on the target's password."""
    with patch("src.api.routes.platform_admin.supabase_select", side_effect=_fake_select_active_target), \
         patch("src.api.routes.platform_admin.is_super_admin",
               side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL), \
         patch("src.services.supabase_service.supabase_service.log_audit", new=AsyncMock(return_value=None)):
        mint_resp = client.post(
            f"/api/v2/admin/tenants/{TARGET_TENANT_ID}/impersonate",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
    assert mint_resp.status_code == 200, mint_resp.text
    body = mint_resp.json()
    assert body["tenant"]["id"] == TARGET_TENANT_ID
    assert body["tenant"]["email"] == TARGET_EMAIL
    assert body["expires_in"] <= 30 * 60  # short-lived, not a normal session

    impersonation_token = body["access_token"]

    whoami_resp = client.get("/api/v2/_whoami", headers={"Authorization": f"Bearer {impersonation_token}"})
    assert whoami_resp.status_code == 200, whoami_resp.text
    who = whoami_resp.json()
    assert who["tenant_id"] == TARGET_TENANT_ID
    assert who["email"] == TARGET_EMAIL
    # Never resolves to the admin's own identity.
    assert who["tenant_id"] != ADMIN_TENANT_ID


def test_impersonation_token_cannot_reach_admin_only_routes():
    """An impersonation token's `email` claim is the impersonated customer's
    email, not the admin's — so it must fail is_super_admin the same way any
    other customer token would, blocking any privilege-chaining attempt to
    use it for a second, nested impersonation or other admin action."""
    token = real_auth_service.create_impersonation_token(
        tenant_id=TARGET_TENANT_ID, email=TARGET_EMAIL, admin_email=ADMIN_EMAIL,
    )
    with patch("src.api.routes.platform_admin.is_super_admin",
               side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL):
        resp = client.get("/api/v2/admin/tenants", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_impersonation_token_payload_carries_no_password():
    """The minted token's own claims must never contain a password/secret —
    it's built purely from tenant_id/email plus the acting admin's email."""
    import jwt as pyjwt
    token = real_auth_service.create_impersonation_token(
        tenant_id=TARGET_TENANT_ID, email=TARGET_EMAIL, admin_email=ADMIN_EMAIL,
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert payload["type"] == "impersonation"
    assert payload["sub"] == TARGET_TENANT_ID
    assert payload["email"] == TARGET_EMAIL
    assert payload["admin_email"] == ADMIN_EMAIL
    assert not any("password" in str(k).lower() for k in payload.keys())
