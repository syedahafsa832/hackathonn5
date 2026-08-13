"""
P0 fix: shopify_service.get_client_for_tenant() must resolve the connected
Shopify brand for the AUTHENTICATED TENANT specifically - not an arbitrary,
unscoped "first active brand" system-wide.

Root cause (found via a live approval failure on the real dashboard): the
brands-table lookup was `supabase_select("brands", {"is_active": "eq.true",
"limit": "1"})` - no tenant_id filter at all. For the real Hafsa Clothing
tenant this returned a *different* tenant's brand (whichever happened to be
"is_active" and come back first), which lacked Shopify credentials, so the
function fell through to the legacy `tenants.shopify_connected` flag - which
is false for this tenant, since its real connection lives in the newer
`brands` table. Result: "No Shopify store connected" even though the
tenant's brand IS connected. Confirmed live: 5 brands system-wide have
is_active=true and none of them belong to the Hafsa Clothing tenant.

Fix: scope the brands lookup by tenant_id (matching the pattern already used
in saas_settings.py, v2_email_filter.py, v2_quarantine.py, etc.), prefer an
active brand but fall back to any brand owned by the tenant, and pick the
one that is actually shopify_connected - since "is_active" doesn't
correlate with Shopify connection status.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.shopify_service import shopify_service, ShopifyError  # noqa: E402

HAFSA_TENANT = "da1561e0-9369-4caf-9fb9-acc48d1e3a83"
OTHER_TENANT = "b0d16c99-f1f2-437e-aade-c2830b8d6485"

HAFSA_BRAND = {
    "id": "d082c4df-e49f-4fec-9476-f546f2523464",
    "tenant_id": HAFSA_TENANT,
    "name": "Hafsa Clothing",
    "is_active": False,  # the exact real-world condition that broke the old lookup
    "shopify_connected": True,
    "shopify_domain": "hafsaclothing.myshopify.com",
    "shopify_shop_name": None,
    "shopify_access_token": "encrypted-token-value",
    "shopify_api_version": None,
}

OTHER_ACTIVE_BRAND_NO_SHOPIFY = {
    "id": "f213490b-a446-4e23-a00a-8111b88567d3",
    "tenant_id": OTHER_TENANT,
    "name": "ABZ Agent SDK",
    "is_active": True,
    "shopify_connected": False,
    "shopify_domain": None,
    "shopify_shop_name": None,
    "shopify_access_token": None,
}


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _select_side_effect(all_brands, tenants_table=None):
    """Mimics PostgREST filtering behavior for the queries this function issues."""
    def _select(table, params=None):
        params = params or {}
        if table == "brands":
            rows = [b for b in all_brands if b.get("tenant_id") == params.get("tenant_id", "").replace("eq.", "")]
            if "is_active" in params:
                rows = [b for b in rows if b.get("is_active") is True]
            return rows
        if table == "tenants":
            return tenants_table or []
        return []
    return _select


# ── HafsaClothing resolves its own connected brand ──────────────────────────

def test_tenant_resolves_its_own_connected_brand_even_if_inactive():
    """The exact real-world bug: the connected brand has is_active=False, so
    the primary (is_active=true) query returns nothing - the fallback to
    "any brand owned by this tenant" must still find it."""
    with patch("src.services.shopify_service.supabase_select", side_effect=_select_side_effect([HAFSA_BRAND, OTHER_ACTIVE_BRAND_NO_SHOPIFY])), \
         patch("src.services.shopify_service.decrypt_token", return_value="shpat_real_token"):
        client = run(shopify_service.get_client_for_tenant(HAFSA_TENANT))

    assert client.shop_domain == "hafsaclothing.myshopify.com"


# ── Cross-tenant isolation ───────────────────────────────────────────────────

def test_other_tenant_cannot_resolve_hafsa_clothings_brand():
    """A different tenant, with no connected brand of its own and no legacy
    tenants-row connection, must NOT be handed HafsaClothing's Shopify
    client - even though HafsaClothing's brand is 'is_active=true'-eligible
    in the old, unscoped query."""
    with patch("src.services.shopify_service.supabase_select", side_effect=_select_side_effect([HAFSA_BRAND, OTHER_ACTIVE_BRAND_NO_SHOPIFY], tenants_table=[])), \
         patch("src.services.shopify_service.decrypt_token", return_value="shpat_real_token"):
        try:
            run(shopify_service.get_client_for_tenant(OTHER_TENANT))
            assert False, "expected ShopifyError — must not resolve another tenant's brand"
        except ShopifyError as e:
            assert "not connected" in e.message.lower() or "not found" in e.message.lower()


# ── No connected brand fails safely ──────────────────────────────────────────

def test_tenant_with_no_connected_brand_and_no_legacy_connection_fails_safely():
    with patch("src.services.shopify_service.supabase_select", side_effect=_select_side_effect(
            [OTHER_ACTIVE_BRAND_NO_SHOPIFY],
            tenants_table=[{"id": OTHER_TENANT, "shopify_connected": False}])), \
         patch("src.services.shopify_service.decrypt_token", return_value=""):
        try:
            run(shopify_service.get_client_for_tenant(OTHER_TENANT))
            assert False, "expected ShopifyError"
        except ShopifyError as e:
            assert "No Shopify store connected" in e.message


# ── Legacy tenants-table fallback still works ────────────────────────────────

def test_legacy_tenant_with_no_brands_row_still_resolves_via_tenants_table():
    """A tenant that predates the brands-table model (no brands row at all)
    must still resolve through the untouched legacy fallback."""
    legacy_tenant_id = "legacy-tenant-1"
    with patch("src.services.shopify_service.supabase_select", side_effect=_select_side_effect(
            [],
            tenants_table=[{
                "id": legacy_tenant_id,
                "shopify_connected": True,
                "shopify_domain": "legacy-store.myshopify.com",
                "shopify_access_token": "encrypted-legacy-token",
                "shopify_api_version": None,
            }])), \
         patch("src.services.shopify_service.decrypt_token", return_value="shpat_legacy_token"):
        client = run(shopify_service.get_client_for_tenant(legacy_tenant_id))

    assert client.shop_domain == "legacy-store.myshopify.com"
