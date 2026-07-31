"""
Registration Brand Auto-Creation Tests (C2 fix)
==================================================
Covers: AuthService.register() must create a default brand for every new
tenant (so Gmail/Shopify connect, which both 400 without one, work
immediately after signup), must not silently swallow a brand-creation
failure (elevated to logger.error), and must still let registration itself
succeed even if brand creation fails (a partially-broken account beats a
fully-failed signup).

All Supabase calls are mocked — no live DB required.
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.auth_service import AuthService  # noqa: E402


def _run_register(insert_side_effect, email="new.tenant@example.com", company_name=None):
    auth_service = AuthService()
    with patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=insert_side_effect), \
         patch.object(AuthService, "_store_refresh_token", new=AsyncMock(return_value=None)):
        import asyncio
        return asyncio.run(auth_service.register(email=email, password="supersecret123", company_name=company_name))


def test_register_creates_a_default_brand_for_the_new_tenant():
    calls = []

    def fake_insert(table, data):
        calls.append((table, data))
        if table == "tenants":
            return {"id": "tenant-1", **data}
        if table == "brands":
            return {"id": "brand-1", **data}
        return {"id": "row-1"}

    result = _run_register(fake_insert, email="jane@acme.com", company_name="Acme Co")

    assert result["success"] is True
    brand_calls = [c for c in calls if c[0] == "brands"]
    assert len(brand_calls) == 1
    brand_data = brand_calls[0][1]
    assert brand_data["tenant_id"] == "tenant-1"
    assert brand_data["name"] == "Acme Co"
    assert brand_data["is_active"] is True


def test_default_brand_gets_a_non_null_support_email():
    """support_email is NOT NULL on brands' original schema — the insert must
    supply something, or it fails the same way the reported bug did."""
    calls = []

    def fake_insert(table, data):
        calls.append((table, data))
        if table == "tenants":
            return {"id": "tenant-1", **data}
        return {"id": "brand-1", **data}

    _run_register(fake_insert, email="jane@acme.com")

    brand_data = next(d for t, d in calls if t == "brands")
    assert brand_data.get("support_email") == "jane@acme.com"


def test_brand_creation_failure_does_not_fail_registration_but_is_logged_loudly(caplog):
    def fake_insert(table, data):
        if table == "tenants":
            return {"id": "tenant-1", **data}
        if table == "brands":
            raise Exception('null value in column "shopify_shop_name" violates not-null constraint')
        return {"id": "row-1"}

    with caplog.at_level("ERROR"):
        result = _run_register(fake_insert, email="jane@acme.com")

    # Registration itself must still succeed — a recoverable brand-less
    # tenant beats a fully-failed signup.
    assert result["success"] is True
    assert result["tenant_id"] == "tenant-1"

    # But the failure must be loud (ERROR, not the old silent WARNING), and
    # actionable — mentions the tenant and that Gmail/Shopify will break.
    error_messages = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any("BRAND CREATION FAILED" in m for m in error_messages)
    assert any("tenant-1" in m for m in error_messages)
