"""
Shopify sync cross-tenant ID collision (P0 tenant-isolation audit).

Shopify's numeric product/order/variant/line-item IDs are per-shop, not
globally unique — two different tenants' independent Shopify stores can
have colliding IDs (especially low IDs in dev/test stores). sync_single_order
and sync_single_product upserted by matching ONLY on the Shopify ID
(shopify_order_id / shopify_id / shopify_line_item_id / shopify_variant_id),
with no store_id/order_id/product_id in the same lookup — so tenant A's
sync could silently find and overwrite tenant B's row on a collision,
re-attributing it to A's store_id. Fixed by scoping every upsert lookup by
the already-resolved owning ID (store_id for orders/products, order_id for
line items, product_id for variants).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.shopify_sync import ShopifySyncService  # noqa: E402


def _run(coro):
    import asyncio
    return asyncio.run(coro)


SHOPIFY_ORDER_ID = 5551000  # colliding ID: exists for a DIFFERENT store already


def test_order_with_colliding_shopify_id_from_another_store_is_not_overwritten():
    """The core regression: tenant A's sync must not find/update tenant B's
    order row just because they share the same (per-shop, not globally
    unique) Shopify order ID."""
    other_store_order = {"id": "order-row-store-B", "store_id": "store-B", "shopify_order_id": SHOPIFY_ORDER_ID}

    def fake_select(table, params=None):
        params = params or {}
        if table != "orders":
            return []
        if params.get("shopify_order_id") != f"eq.{SHOPIFY_ORDER_ID}":
            return []
        wanted_store = params.get("store_id", "").removeprefix("eq.")
        if wanted_store and wanted_store != other_store_order["store_id"]:
            return []  # the fix: store_id filter excludes store B's row for store A's sync
        return [other_store_order]

    inserted = {}

    def fake_insert(table, data):
        row = {**data, "id": "order-row-store-A-new"}
        if table == "orders":
            inserted["order"] = row
        return row

    service = ShopifySyncService()
    shopify_order = {
        "id": SHOPIFY_ORDER_ID, "order_number": 1042, "fulfillment_status": "unfulfilled",
        "total_price": "39.99", "fulfillments": [], "customer": {"email": "buyer@example.com"},
        "line_items": [],
    }

    with patch("src.services.shopify_sync.supabase_select", side_effect=fake_select), \
         patch("src.services.shopify_sync.supabase_insert", side_effect=fake_insert), \
         patch("src.services.shopify_sync.supabase_update") as mock_update:
        _run(service.sync_single_order(shopify_order, store_id="store-A"))

    mock_update.assert_not_called()  # store B's row was never touched
    assert inserted["order"]["store_id"] == "store-A"  # a new, correctly-owned row was created instead


def test_order_with_matching_store_id_is_correctly_updated_not_duplicated():
    """Positive control: a real repeat sync for the SAME store must still
    update its own existing order, not fork a duplicate."""
    own_order = {"id": "order-row-store-A", "store_id": "store-A", "shopify_order_id": SHOPIFY_ORDER_ID}

    def fake_select(table, params=None):
        params = params or {}
        if table != "orders":
            return []
        if params.get("shopify_order_id") == f"eq.{SHOPIFY_ORDER_ID}" and params.get("store_id") == "eq.store-A":
            return [own_order]
        return []

    service = ShopifySyncService()
    shopify_order = {
        "id": SHOPIFY_ORDER_ID, "order_number": 1042, "fulfillment_status": "unfulfilled",
        "total_price": "39.99", "fulfillments": [], "customer": {"email": "buyer@example.com"},
        "line_items": [],
    }

    with patch("src.services.shopify_sync.supabase_select", side_effect=fake_select), \
         patch("src.services.shopify_sync.supabase_insert") as mock_insert, \
         patch("src.services.shopify_sync.supabase_update") as mock_update:
        _run(service.sync_single_order(shopify_order, store_id="store-A"))

    mock_insert.assert_not_called()
    mock_update.assert_called_once()
    assert mock_update.call_args[0][1] == {"id": "eq.order-row-store-A"}


SHOPIFY_PRODUCT_ID = 8881000


def test_product_with_colliding_shopify_id_from_another_store_is_not_overwritten():
    """Same collision regression for products."""
    other_store_product = {"id": "product-row-store-B", "store_id": "store-B", "shopify_id": SHOPIFY_PRODUCT_ID}

    def fake_select(table, params=None):
        params = params or {}
        if table != "products":
            return []
        if params.get("shopify_id") != f"eq.{SHOPIFY_PRODUCT_ID}":
            return []
        wanted_store = params.get("store_id", "").removeprefix("eq.")
        if wanted_store and wanted_store != other_store_product["store_id"]:
            return []
        return [other_store_product]

    inserted = {}

    def fake_insert(table, data):
        row = {**data, "id": "product-row-store-A-new"}
        if table == "products":
            inserted["product"] = row
        return row

    service = ShopifySyncService()
    shopify_product = {"id": SHOPIFY_PRODUCT_ID, "title": "Test Shirt", "body_html": "", "tags": "", "variants": []}

    with patch("src.services.shopify_sync.supabase_select", side_effect=fake_select), \
         patch("src.services.shopify_sync.supabase_insert", side_effect=fake_insert), \
         patch("src.services.shopify_sync.supabase_update") as mock_update, \
         patch.object(service, "_get_embedding", new=AsyncMock(return_value=None)):
        _run(service.sync_single_product(shopify_product, store_id="store-A"))

    mock_update.assert_not_called()
    assert inserted["product"]["store_id"] == "store-A"
