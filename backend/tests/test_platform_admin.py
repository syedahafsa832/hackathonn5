"""
Platform Admin Access Control Tests (item 3)
===============================================
Confirms /api/v2/admin/* is actually gated server-side: a non-admin tenant's
real JWT must be rejected (403), and only the confirmed admin email
(syedahafsa1983@gmail.com) gets through. Also proves the endpoint's data
shape (tenants + brand connection flags + ticket counts) is correct.
"""
import os
import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.platform_admin import router as platform_admin_router  # noqa: E402
from src.services.auth_service import auth_service as real_auth_service  # noqa: E402

app = FastAPI()
app.include_router(platform_admin_router, prefix="/api/v2")
client = TestClient(app)

ADMIN_TENANT_ID = "56dafdeb-bd22-4582-9f3a-3616009a43e4"
ADMIN_EMAIL = "syedahafsa1983@gmail.com"
NON_ADMIN_TENANT_ID = "99999999-9999-9999-9999-999999999999"
NON_ADMIN_EMAIL = "random.customer@example.com"


def _fake_select(table, params=None):
    # get_current_tenant derives tenant_id/email straight from the JWT payload
    # (see tenant_auth.py) — it never queries the DB, so there's no auth-lookup
    # branch needed here, only the route body's own data queries below.
    params = params or {}
    if table == "tenants":
        return [
            {"id": ADMIN_TENANT_ID, "email": ADMIN_EMAIL, "company_name": "HafsaClothing",
             "plan": "enterprise", "is_active": True, "created_at": "2026-06-03T00:00:00Z", "last_login_at": None},
        ]
    if table == "brands":
        return [{"id": "brand-1", "tenant_id": ADMIN_TENANT_ID, "name": "HafsaClothing",
                  "gmail_connected": True, "shopify_connected": True, "is_active": True}]
    if table == "tickets":
        return [{"id": "t1", "brand_id": "brand-1"}, {"id": "t2", "brand_id": "brand-1"}]
    return []


def test_non_admin_jwt_gets_403_not_data():
    token = real_auth_service.create_access_token(NON_ADMIN_TENANT_ID, NON_ADMIN_EMAIL)
    with patch("src.api.middleware.tenant_auth.auth_service.decode_token", side_effect=real_auth_service.decode_token), \
         patch("src.api.routes.platform_admin.is_super_admin", side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL):
        resp = client.get("/api/v2/admin/tenants", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "tenants" not in resp.json()


def test_no_jwt_at_all_is_rejected():
    resp = client.get("/api/v2/admin/tenants")
    assert resp.status_code == 401


def test_admin_jwt_gets_tenant_data_with_connection_status_and_ticket_counts():
    token = real_auth_service.create_access_token(ADMIN_TENANT_ID, ADMIN_EMAIL)
    with patch("src.api.routes.platform_admin.supabase_select", side_effect=_fake_select), \
         patch("src.api.routes.platform_admin.is_super_admin", side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL):
        resp = client.get("/api/v2/admin/tenants", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 1
    row = data["tenants"][0]
    assert row["email"] == ADMIN_EMAIL
    assert row["plan"] == "enterprise"
    assert row["ticket_count"] == 2
    assert row["brands"][0]["gmail_connected"] is True
    assert row["brands"][0]["shopify_connected"] is True
