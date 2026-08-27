"""
Shopify webhook store attribution (P0 tenant-isolation audit).

process_shopify_event() used to hardcode store_id to the all-zero
placeholder UUID for every products/update webhook, regardless of which
shop actually sent it — every tenant's product updates were silently
attributed to the same fake store. Fixed by resolving the real owning
brand from the X-Shopify-Shop-Domain header (which Shopify always sends
and which is never client-controlled independent of the HMAC-verified
request), and skipping processing entirely when no matching brand is
found rather than falling back to a shared placeholder.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.webhooks import shopify as shopify_webhook_module  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_product_update_resolves_store_id_from_shop_domain_header():
    brand_row = {"id": "brand-real-owner", "shopify_domain": "realshop.myshopify.com"}

    def fake_select(table, params=None):
        params = params or {}
        if table == "webhook_events":
            return []  # not a duplicate
        if table == "brands" and params.get("shopify_domain") == "eq.realshop.myshopify.com":
            return [brand_row]
        return []

    sync_mock = AsyncMock()
    with patch("src.api.routes.webhooks.shopify.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.webhooks.shopify.supabase_insert"), \
         patch("src.services.shopify_sync.shopify_sync_service.sync_single_product", new=sync_mock):
        _run(shopify_webhook_module.process_shopify_event(
            "products/update", {"id": 123}, "evt-1", "realshop.myshopify.com",
        ))

    sync_mock.assert_called_once_with({"id": 123}, store_id="brand-real-owner")


def test_product_update_with_unknown_shop_domain_is_skipped_not_misattributed():
    """No matching brand for the shop domain — must fail closed (skip),
    never fall back to a shared/placeholder store_id that would attribute
    this shop's product data to every other tenant using that placeholder."""
    def fake_select(table, params=None):
        params = params or {}
        if table == "webhook_events":
            return []
        if table == "brands":
            return []  # no brand registered for this domain
        return []

    sync_mock = AsyncMock()
    with patch("src.api.routes.webhooks.shopify.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.webhooks.shopify.supabase_insert"), \
         patch("src.services.shopify_sync.shopify_sync_service.sync_single_product", new=sync_mock):
        _run(shopify_webhook_module.process_shopify_event(
            "products/update", {"id": 123}, "evt-2", "unknown-shop.myshopify.com",
        ))

    sync_mock.assert_not_called()


def test_product_update_never_uses_the_old_placeholder_store_id():
    """Regression pin: even with a resolvable brand, the sync call must
    never be made with the old hardcoded all-zero placeholder."""
    brand_row = {"id": "brand-real-owner", "shopify_domain": "realshop.myshopify.com"}

    def fake_select(table, params=None):
        params = params or {}
        if table == "webhook_events":
            return []
        if table == "brands":
            return [brand_row]
        return []

    sync_mock = AsyncMock()
    with patch("src.api.routes.webhooks.shopify.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.webhooks.shopify.supabase_insert"), \
         patch("src.services.shopify_sync.shopify_sync_service.sync_single_product", new=sync_mock):
        _run(shopify_webhook_module.process_shopify_event(
            "products/update", {"id": 123}, "evt-3", "realshop.myshopify.com",
        ))

    called_store_id = sync_mock.call_args.kwargs["store_id"]
    assert called_store_id != "00000000-0000-0000-0000-000000000000"
