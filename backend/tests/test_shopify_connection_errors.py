"""
"Test Shopify Connection" must never show a non-technical merchant a raw
HTTP status/exception (previously: "API error: 401"). Covers both the
service layer (BrandManager.test_connection/_validate_shopify_credentials)
and the route's own outer exception handler
(POST /api/brands/{id}/test-connection).
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.services.brand_manager import BrandManager  # noqa: E402
from src.api.routes import brands as brands_module  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

BRAND_ID = "brand-1"
TENANT_ID = "tenant-1"

# Every raw technical fragment that must never reach the merchant-facing message.
_FORBIDDEN_FRAGMENTS = ["401", "403", "404", "500", "API error", "HTTP error", "Traceback", "Exception"]


def _assert_no_technical_leakage(message: str):
    assert message, "error message must not be empty"
    for fragment in _FORBIDDEN_FRAGMENTS:
        assert fragment not in message, f"GAP: raw technical fragment {fragment!r} leaked into merchant-facing message: {message!r}"


def _fake_response(status_code, text="error"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = {"shop": {}}
    return resp


@pytest.mark.asyncio
async def test_not_connected_shows_human_readable_message_not_a_crash():
    manager = BrandManager()  # fresh instance - never shares the singleton's brand cache
    brand = {"id": BRAND_ID, "shopify_shop_name": None, "_decrypted_token": None}
    with patch.object(manager, "get_brand", return_value=brand):
        result = await manager.test_connection(BRAND_ID)

    assert result["success"] is False
    _assert_no_technical_leakage(result["error"])
    assert "connect" in result["error"].lower()


@pytest.mark.asyncio
async def test_expired_or_invalid_401_shows_reconnect_message():
    manager = BrandManager()
    brand = {"id": BRAND_ID, "shopify_shop_name": "teststore", "_decrypted_token": "tok"}
    with patch.object(manager, "get_brand", return_value=brand), \
         patch("src.services.brand_manager.requests.get", return_value=_fake_response(401)):
        result = await manager.test_connection(BRAND_ID)

    assert result["success"] is False
    _assert_no_technical_leakage(result["error"])
    assert "reconnect" in result["error"].lower()


@pytest.mark.asyncio
async def test_permission_403_shows_permission_message():
    manager = BrandManager()
    brand = {"id": BRAND_ID, "shopify_shop_name": "teststore", "_decrypted_token": "tok"}
    with patch.object(manager, "get_brand", return_value=brand), \
         patch("src.services.brand_manager.requests.get", return_value=_fake_response(403)):
        result = await manager.test_connection(BRAND_ID)

    assert result["success"] is False
    _assert_no_technical_leakage(result["error"])
    assert "permission" in result["error"].lower()


@pytest.mark.asyncio
async def test_temporary_5xx_shows_temporary_problem_message():
    manager = BrandManager()
    brand = {"id": BRAND_ID, "shopify_shop_name": "teststore", "_decrypted_token": "tok"}
    with patch.object(manager, "get_brand", return_value=brand), \
         patch("src.services.brand_manager.requests.get", return_value=_fake_response(503)):
        result = await manager.test_connection(BRAND_ID)

    assert result["success"] is False
    _assert_no_technical_leakage(result["error"])
    assert "try again" in result["error"].lower()


@pytest.mark.asyncio
async def test_network_exception_never_leaks_raw_exception_text():
    manager = BrandManager()
    brand = {"id": BRAND_ID, "shopify_shop_name": "bogus-store-name", "_decrypted_token": "tok"}
    raw_error = "HTTPSConnectionPool(host='bogus-store-name.myshopify.com'): [Errno -2] Name or service not known"
    with patch.object(manager, "get_brand", return_value=brand), \
         patch("src.services.brand_manager.requests.get", side_effect=Exception(raw_error)):
        result = await manager.test_connection(BRAND_ID)

    assert result["success"] is False
    _assert_no_technical_leakage(result["error"])
    assert "bogus-store-name" not in result["error"]
    assert "Errno" not in result["error"]


@pytest.mark.asyncio
async def test_successful_connection_still_returns_shop_details():
    manager = BrandManager()
    brand = {"id": BRAND_ID, "shopify_shop_name": "teststore", "_decrypted_token": "tok"}
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"shop": {"name": "Test Store", "domain": "teststore.myshopify.com", "plan_name": "basic", "currency": "USD"}}
    with patch.object(manager, "get_brand", return_value=brand), \
         patch("src.services.brand_manager.requests.get", return_value=ok_resp):
        result = await manager.test_connection(BRAND_ID)

    assert result["success"] is True
    assert result["shop_name"] == "Test Store"


# ── Route-level: the outer exception handler must not leak str(e) either ──

app = FastAPI()
app.include_router(brands_module.router, prefix="/api")
client = TestClient(app)


def _override_tenant(tenant_id=TENANT_ID):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def _with_tenant(fn, tenant_id=TENANT_ID):
    app.dependency_overrides[get_current_tenant] = _override_tenant(tenant_id)
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


def test_route_never_leaks_raw_exception_on_unexpected_failure():
    with patch("src.api.routes.brands.supabase_select", return_value=[{"id": BRAND_ID, "tenant_id": TENANT_ID}]), \
         patch("src.services.brand_manager.brand_manager.test_connection", side_effect=RuntimeError("db connection refused on host 10.0.0.5:5432")):
        resp = _with_tenant(lambda: client.post(f"/api/brands/{BRAND_ID}/test-connection"))

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    _assert_no_technical_leakage(detail)
    assert "10.0.0.5" not in detail
    assert "db connection refused" not in detail
