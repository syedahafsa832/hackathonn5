"""
Shopify connect -> Knowledge Base auto-ingestion.

Merchants shouldn't have to separately remember to visit onboarding's
"Import your store" step after connecting Shopify. _connect_shopify_credentials
(shared by the OAuth callback in shopify_auth.py, the manual access-token
route below, and saas_settings.py's legacy Settings connect) now calls
_start_shopify_import_if_needed right after a successful connection - the
exact same idempotent, scope-gated kickoff POST /shopify/import already
used. These tests prove that wiring, and that it stays idempotent/brand-
scoped when called from the connect path specifically.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
import src.services.shopify_import_service as import_mod  # noqa: E402

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ID = "brand-1"
TENANT_ID = "tenant-1"


@pytest.fixture(autouse=True)
def _reset_state():
    import_mod._import_status.pop(BRAND_ID, None)
    yield
    import_mod._import_status.pop(BRAND_ID, None)


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


def _fake_select(brand, kb_sources=None):
    def fn(table, params=None):
        params = params or {}
        if table == "brands":
            wanted_tenant = (params.get("tenant_id") or "").replace("eq.", "")
            if wanted_tenant and wanted_tenant != brand.get("tenant_id"):
                return []
            return [brand]
        if table == "knowledge_base_sources":
            rows = kb_sources if kb_sources is not None else []
            if params.get("status") == "eq.completed":
                rows = [r for r in rows if r.get("status") == "completed"]
            return rows
        return []
    return fn


def _fake_shopify_client():
    fake = MagicMock()
    fake.shop_domain = "test.myshopify.com"
    fake.validate_connection = AsyncMock(return_value={"success": True, "shop_name": "Test Store"})
    return fake


def test_connecting_shopify_auto_starts_import():
    """POST /{brand_id}/shopify/connect (the manual access-token path,
    shared with the OAuth callback via _connect_shopify_credentials) must
    kick off ingestion automatically right after a successful connection."""
    brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "shopify_connected": False}

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(brand)), \
         patch("src.api.routes.v2_brands.supabase_update"), \
         patch("src.api.routes.v2_brands.encrypt_token", return_value="enc"), \
         patch("src.services.shopify_service.ShopifyClient", return_value=_fake_shopify_client()), \
         patch("src.services.shopify_scope_service.check_and_store_scopes",
               new=AsyncMock(return_value={"granted_scopes": ["read_products", "read_content"]})), \
         patch("src.services.supabase_service.supabase_service.log_onboarding_event", new=AsyncMock()), \
         patch.object(import_mod, "run_shopify_import", new=AsyncMock()) as mock_run:
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/shopify/connect",
            json={"shop_domain": "test.myshopify.com", "access_token": "shpat_test123456789"},
        ))

    assert resp.status_code == 200, resp.text
    mock_run.assert_called_once_with(BRAND_ID)


def test_connecting_shopify_again_does_not_duplicate_a_completed_import():
    """Reconnecting (or the OAuth flow re-running after a token refresh)
    must not re-trigger the importer once the brand's knowledge base is
    already populated - the same idempotency guard POST /shopify/import
    uses on its own, reused here."""
    brand = {"id": BRAND_ID, "tenant_id": TENANT_ID, "shopify_connected": True}
    completed_sources = [{"id": "s1", "status": "completed", "brand_id": BRAND_ID}]

    with patch("src.api.routes.v2_brands.supabase_select", side_effect=_fake_select(brand, kb_sources=completed_sources)), \
         patch("src.api.routes.v2_brands.supabase_update"), \
         patch("src.api.routes.v2_brands.encrypt_token", return_value="enc"), \
         patch("src.services.shopify_service.ShopifyClient", return_value=_fake_shopify_client()), \
         patch("src.services.shopify_scope_service.check_and_store_scopes",
               new=AsyncMock(return_value={"granted_scopes": ["read_products", "read_content"]})), \
         patch("src.services.supabase_service.supabase_service.log_onboarding_event", new=AsyncMock()), \
         patch.object(import_mod, "run_shopify_import", new=AsyncMock()) as mock_run:
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/shopify/connect",
            json={"shop_domain": "test.myshopify.com", "access_token": "shpat_test123456789"},
        ))

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()


def test_connect_credentials_kicks_off_import_for_the_connected_brand():
    """Unit-level check on _connect_shopify_credentials itself (shared by
    the OAuth callback and the manual-token route): a successful connection
    must call the auto-import kickoff for the brand that was actually
    connected, with the scopes it just checked - not force a second, live
    scope re-check."""
    import asyncio
    from src.api.routes import v2_brands as mod

    with patch.object(mod, "_start_shopify_import_if_needed",
                       new=AsyncMock(return_value={"success": True, "status": "running"})) as mock_start, \
         patch.object(mod.shopify_scope_service, "check_and_store_scopes",
                       new=AsyncMock(return_value={"granted_scopes": ["read_products", "read_content"]})), \
         patch.object(mod.supabase_service, "log_onboarding_event", new=AsyncMock()), \
         patch("src.services.shopify_service.ShopifyClient", return_value=_fake_shopify_client()), \
         patch.object(mod, "supabase_update"), \
         patch.object(mod, "encrypt_token", return_value="enc"):
        result = asyncio.run(
            mod._connect_shopify_credentials(BRAND_ID, TENANT_ID, "test.myshopify.com", "shpat_x")
        )

    assert result["success"] is True
    mock_start.assert_awaited_once()
    assert mock_start.await_args.args[0] == BRAND_ID
    passed_brand = mock_start.await_args.args[1]
    assert passed_brand["shopify_granted_scopes"] == ["read_products", "read_content"]
