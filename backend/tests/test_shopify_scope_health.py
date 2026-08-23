"""
Shopify scope verification & connection health tests
=======================================================
Covers shopify_scope_service.py (fetch/compare/store granted scopes) and
the v2_brands.py route-level wiring: the connect-time scope check, the
pre-import gate that declines to start a doomed run instead of showing
"Nothing found to import", the /shopify/health endpoint, and that a brand
with no scope data on record (pre-dating this feature) never crashes any
of these instead of just degrading. All Shopify/Supabase calls are mocked
— no live services required.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("ENCRYPTION_SECRET", "test-encryption-secret-do-not-use-in-prod")

import src.services.shopify_scope_service as scope_mod  # noqa: E402
from src.services.auth_service import auth_service  # noqa: E402

TENANT_ID = "33333333-3333-3333-3333-333333333333"
BRAND_ID = "44444444-4444-4444-4444-444444444444"

app = FastAPI()
from src.api.routes.v2_brands import router as v2_brands_router  # noqa: E402
app.include_router(v2_brands_router, prefix="/api/v2")
client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_blocked_state():
    scope_mod._blocked_imports.clear()
    yield
    scope_mod._blocked_imports.clear()


def _auth_header():
    token = auth_service.create_access_token(TENANT_ID, "owner@example.com")
    return {"Authorization": f"Bearer {token}"}


def _brand(**overrides):
    return {
        "id": BRAND_ID, "tenant_id": TENANT_ID, "name": "Test Brand",
        "is_active": True, "shopify_connected": True,
        "shopify_domain": "test-shop.myshopify.com",
        "shopify_access_token": "encrypted-token",
        "shopify_granted_scopes": None, "shopify_app_name": None,
        **overrides,
    }


# ─── 1. fetch/compare scopes in isolation ────────────────────────────────

class TestMissingScopes:
    def test_all_required_scopes_present(self):
        granted = ["read_products", "read_content", "read_orders", "read_customers"]
        assert scope_mod.missing_scopes(granted, scope_mod.IMPORT_SCOPES) == []

    def test_missing_read_products_only(self):
        granted = ["read_content", "read_orders"]
        missing = scope_mod.missing_scopes(granted, scope_mod.IMPORT_SCOPES)
        assert missing == ["read_products"]

    def test_missing_read_content_only(self):
        granted = ["read_products", "read_orders"]
        missing = scope_mod.missing_scopes(granted, scope_mod.IMPORT_SCOPES)
        assert missing == ["read_content"]

    def test_missing_both_import_scopes(self):
        granted = ["read_customers", "read_orders"]
        missing = scope_mod.missing_scopes(granted, scope_mod.IMPORT_SCOPES)
        assert set(missing) == {"read_products", "read_content"}

    def test_none_granted_treated_as_all_missing(self):
        """A brand with no scope check on record yet — must not crash."""
        assert scope_mod.missing_scopes(None, scope_mod.IMPORT_SCOPES) == scope_mod.IMPORT_SCOPES


# ─── 2. Pre-import gate ───────────────────────────────────────────────────

class TestImportGate:
    def test_all_scopes_granted_starts_import(self):
        brand = _brand(shopify_granted_scopes=["read_products", "read_content", "read_orders"])
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
             patch("src.api.routes.v2_brands.supabase_service.log_onboarding_event", new=AsyncMock()), \
             patch("src.api.routes.v2_brands.shopify_import_service.run_shopify_import", new=AsyncMock()) as mock_run, \
             patch("src.api.routes.v2_brands.shopify_import_service.get_import_status", return_value="not_started"):
            resp = client.post(f"/api/v2/brands/{BRAND_ID}/shopify/import", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "running"
        mock_run.assert_called_once()

    def test_missing_read_products_still_starts_import(self):
        """Partial scopes (read_content granted, read_products missing) —
        the importer can still pull pages/policies, so this must NOT be
        blocked; it should start and degrade per-resource like before."""
        brand = _brand(shopify_granted_scopes=["read_content", "read_orders"])
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
             patch("src.api.routes.v2_brands.supabase_service.log_onboarding_event", new=AsyncMock()), \
             patch("src.api.routes.v2_brands.shopify_import_service.run_shopify_import", new=AsyncMock()) as mock_run, \
             patch("src.api.routes.v2_brands.shopify_import_service.get_import_status", return_value="not_started"):
            resp = client.post(f"/api/v2/brands/{BRAND_ID}/shopify/import", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "running"
        mock_run.assert_called_once()

    def test_missing_read_content_still_starts_import(self):
        brand = _brand(shopify_granted_scopes=["read_products", "read_orders"])
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
             patch("src.api.routes.v2_brands.supabase_service.log_onboarding_event", new=AsyncMock()), \
             patch("src.api.routes.v2_brands.shopify_import_service.run_shopify_import", new=AsyncMock()) as mock_run, \
             patch("src.api.routes.v2_brands.shopify_import_service.get_import_status", return_value="not_started"):
            resp = client.post(f"/api/v2/brands/{BRAND_ID}/shopify/import", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "running"
        mock_run.assert_called_once()

    def test_both_import_scopes_missing_blocks_without_starting(self):
        """Reproduces the real hafsaclothing token today: only
        customers/fulfillments/orders granted, neither read_products nor
        read_content. The route must NOT start run_shopify_import, and must
        return the exact required message instead of a doomed run."""
        brand = _brand(shopify_granted_scopes=["read_customers", "write_customers", "read_orders"])
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
             patch("src.api.routes.v2_brands.supabase_service.log_onboarding_event", new=AsyncMock()), \
             patch("src.api.routes.v2_brands.shopify_import_service.run_shopify_import", new=AsyncMock()) as mock_run, \
             patch("src.api.routes.v2_brands.shopify_import_service.get_import_status", return_value="not_started"):
            resp = client.post(f"/api/v2/brands/{BRAND_ID}/shopify/import", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "blocked_missing_scopes"
        assert set(body["missing_scopes"]) == {"read_products", "read_content"}
        assert body["message"] == (
            "Your Shopify connection works, but additional permissions are required "
            "to import products and store content."
        )
        assert "understand your products, policies, and store information" in body["reason"]
        mock_run.assert_not_called()

    def test_missing_scopes_never_crash_the_import_endpoint(self):
        """No scope check on record at all (brand connected before this
        feature existed) and the live re-check itself fails — the endpoint
        must still respond, not 500."""
        brand = _brand(shopify_granted_scopes=None)
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
             patch("src.api.routes.v2_brands.shopify_import_service._get_client_for_brand", return_value=None), \
             patch("src.api.routes.v2_brands.supabase_service.log_onboarding_event", new=AsyncMock()), \
             patch("src.api.routes.v2_brands.shopify_import_service.run_shopify_import", new=AsyncMock()) as mock_run, \
             patch("src.api.routes.v2_brands.shopify_import_service.get_import_status", return_value="not_started"):
            resp = client.post(f"/api/v2/brands/{BRAND_ID}/shopify/import", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        # No client available -> treated as zero granted scopes -> blocked, not a crash.
        assert resp.json()["status"] == "blocked_missing_scopes"
        mock_run.assert_not_called()

    def test_blocked_state_persists_across_status_polls(self):
        """After a blocked POST /import, GET /import-status must keep
        reporting the block (not silently reset to not_started) so the UI
        doesn't flip back to a misleading blank state on refresh."""
        scope_mod.set_blocked(BRAND_ID, ["read_products", "read_content"])
        brand = _brand()
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]):
            resp = client.get(f"/api/v2/brands/{BRAND_ID}/shopify/import-status", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "blocked_missing_scopes"
        assert set(body["missing_scopes"]) == {"read_products", "read_content"}


# ─── 3. /shopify/health ────────────────────────────────────────────────────

class TestShopifyHealth:
    def test_healthy_when_all_scopes_granted(self):
        brand = _brand(
            shopify_granted_scopes=["read_products", "read_content", "read_orders", "write_orders"],
            shopify_app_name="lastcustomersupport",
        )
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]):
            resp = client.get(f"/api/v2/brands/{BRAND_ID}/shopify/health", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connected"] is True
        assert body["status"] == "healthy"
        assert body["missing_scopes"] == []
        assert body["app_name"] == "lastcustomersupport"
        assert body["domain"] == "test-shop.myshopify.com"

    def test_needs_permission_update_when_scopes_missing(self):
        brand = _brand(shopify_granted_scopes=["read_customers", "read_orders"], shopify_app_name="lastcustomersupport")
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]):
            resp = client.get(f"/api/v2/brands/{BRAND_ID}/shopify/health", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "needs_permission_update"
        assert set(body["missing_scopes"]) == {"read_products", "read_content", "write_orders"}

    def test_not_connected_reports_connected_false_not_error(self):
        brand = _brand(shopify_connected=False)
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]):
            resp = client.get(f"/api/v2/brands/{BRAND_ID}/shopify/health", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"connected": False, "status": "not_connected"}

    def test_no_usable_client_reports_connection_unavailable_not_a_fake_scope_gap(self):
        """Legacy/broken brand: shopify_granted_scopes is None and there's no
        usable client (missing domain/token, or the stored token failed to
        decrypt). Must be reported as "we can't even check" - never silently
        treated as "every required scope confirmed missing", which used to
        make an outright connection problem indistinguishable from a genuine,
        fixable permissions gap."""
        brand = _brand(shopify_granted_scopes=None)
        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
             patch("src.api.routes.v2_brands.shopify_import_service._get_client_for_brand", return_value=None):
            resp = client.get(f"/api/v2/brands/{BRAND_ID}/shopify/health", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connected"] is True
        assert body["status"] == "connection_unavailable"
        assert body["granted_scopes"] == []
        assert body["missing_scopes"] == []

    def test_live_check_failure_reports_check_unavailable_not_needs_permission_update(self):
        """A usable client exists, but the live access_scopes.json call
        itself fails (token revoked, Shopify unreachable) - must not be
        reported as a confirmed missing-scope state."""
        brand = _brand(shopify_granted_scopes=None)
        fake_client = MagicMock()
        fake_client.shop_domain = "test-shop.myshopify.com"
        fake_client.headers = {}

        with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
             patch("src.api.routes.v2_brands.shopify_import_service._get_client_for_brand", return_value=fake_client), \
             patch("src.services.shopify_scope_service.requests.get", side_effect=Exception("connection reset")):
            resp = client.get(f"/api/v2/brands/{BRAND_ID}/shopify/health", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connected"] is True
        assert body["status"] == "check_unavailable"
        assert body["check_error"] == "unreachable"
        assert body["missing_scopes"] == []
        # Never leak the raw exception text into the API response.
        assert "connection reset" not in str(body)


# ─── 4. check_and_store_scopes never raises ───────────────────────────────

class TestCheckAndStoreScopes:
    @pytest.mark.asyncio
    async def test_live_fetch_failure_returns_gracefully(self):
        """The token/domain is bad or unreachable — this must not propagate
        an exception into the caller (connect_shopify / the import gate)."""
        bad_client = MagicMock()
        bad_client.shop_domain = "does-not-exist.myshopify.com"
        bad_client.headers = {}

        with patch("src.services.shopify_scope_service.requests.get", side_effect=Exception("DNS failure")):
            result = await scope_mod.check_and_store_scopes(BRAND_ID, bad_client)

        assert result == {"granted_scopes": None, "app_name": None, "checked_at": None, "error": "unreachable"}

    @pytest.mark.asyncio
    async def test_success_path_stores_scopes_and_clears_any_block(self):
        scope_mod.set_blocked(BRAND_ID, ["read_products", "read_content"])
        ok_client = MagicMock()
        ok_client.shop_domain = "test-shop.myshopify.com"
        ok_client.headers = {}

        fake_resp = MagicMock()
        fake_resp.json.return_value = {"access_scopes": [{"handle": "read_products"}, {"handle": "read_orders"}]}
        fake_resp.raise_for_status.return_value = None

        with patch("src.services.shopify_scope_service.requests.get", return_value=fake_resp), \
             patch("src.services.shopify_scope_service.supabase_update") as mock_update, \
             patch.object(scope_mod, "fetch_app_name", new=AsyncMock(return_value="lastcustomersupport")):
            result = await scope_mod.check_and_store_scopes(BRAND_ID, ok_client)

        assert result["granted_scopes"] == ["read_orders", "read_products"]
        assert result["app_name"] == "lastcustomersupport"
        mock_update.assert_called_once()
        assert scope_mod.get_blocked(BRAND_ID) is None
