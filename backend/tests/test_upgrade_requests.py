"""
Upgrade Request Flow Tests (item 7)
======================================
Covers the customer-facing submission endpoint and the admin-side listing/
activation from platform_admin.py, proving they're wired to the same table.
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

from src.api.routes.upgrade_requests import router as upgrade_router  # noqa: E402
from src.api.routes.platform_admin import router as admin_router  # noqa: E402
from src.services.auth_service import auth_service as real_auth_service  # noqa: E402

app = FastAPI()
app.include_router(upgrade_router, prefix="/api/v2")
app.include_router(admin_router, prefix="/api/v2")
client = TestClient(app)

TENANT_ID = "11111111-2222-3333-4444-555555555555"
ADMIN_EMAIL = "syedahafsa1983@gmail.com"


def test_list_plans_matches_plan_service_source_of_truth():
    resp = client.get("/api/v2/plans")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()["plans"]]
    assert ids == ["free", "starter", "growth", "enterprise"]
    assert "founding_free" not in ids  # legacy plan not offered to new requests


def test_submit_upgrade_request_creates_pending_row():
    token = real_auth_service.create_access_token(TENANT_ID, "customer@example.com")
    captured = {}

    def fake_insert(table, data):
        captured['table'] = table
        captured['data'] = data
        return {"id": "req-1", **data}

    with patch("src.api.routes.upgrade_requests.supabase_insert", side_effect=fake_insert):
        resp = client.post("/api/v2/upgrade-requests",
                            json={"name": "Jane", "email": "jane@example.com", "brand": "Jane's Shop", "plan": "growth"},
                            headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert captured['table'] == "upgrade_requests"
    assert captured['data']['status'] == "pending"
    assert captured['data']['tenant_id'] == TENANT_ID
    assert captured['data']['requested_plan'] == "growth"


def test_submit_upgrade_request_rejects_unknown_plan():
    token = real_auth_service.create_access_token(TENANT_ID, "customer@example.com")
    resp = client.post("/api/v2/upgrade-requests",
                        json={"name": "Jane", "email": "jane@example.com", "plan": "not-a-real-plan"},
                        headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


def test_activate_upgrade_request_requires_admin():
    token = real_auth_service.create_access_token(TENANT_ID, "customer@example.com")
    with patch("src.api.routes.platform_admin.is_super_admin", side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL):
        resp = client.post("/api/v2/admin/upgrade-requests/req-1/activate",
                            headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_activate_upgrade_request_sets_tenant_plan():
    admin_token = real_auth_service.create_access_token("admin-tenant-id", ADMIN_EMAIL)
    updates = []

    def fake_select(table, params=None):
        if table == "upgrade_requests":
            return [{"id": "req-1", "tenant_id": TENANT_ID, "requested_plan": "growth", "status": "pending"}]
        return []

    def fake_update(table, match, data):
        updates.append((table, match, data))
        return [data]

    with patch("src.api.routes.platform_admin.is_super_admin", side_effect=lambda e: (e or "").lower() == ADMIN_EMAIL), \
         patch("src.api.routes.platform_admin.supabase_select", side_effect=fake_select), \
         patch("src.lib.supabase_client.supabase_update", side_effect=fake_update):
        resp = client.post("/api/v2/admin/upgrade-requests/req-1/activate",
                            headers={"Authorization": f"Bearer {admin_token}"})

    assert resp.status_code == 200, resp.text
    tenant_update = next(u for u in updates if u[0] == "tenants")
    assert tenant_update[2]["plan"] == "growth"
